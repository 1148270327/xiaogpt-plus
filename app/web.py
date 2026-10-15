from __future__ import annotations

import asyncio
import hmac
import logging
import math
import secrets
import time
from datetime import date
from pathlib import Path

from aiohttp import ClientSession, web
from miservice import MiNAService

from app import settings
from app.runner import Runner
from mi_login import MiLoginError, login_mi, resolve_device_id
from qwen_bot import QwenBot

logger = logging.getLogger("xiaogpt.app")

STATIC = Path(__file__).parent / "static"
COOKIE = "xg_session"
SESSION_SECONDS = 7 * 24 * 3600
PUBLIC_PATHS = {"/login"}

RUNNER = web.AppKey("runner", Runner)
CONFIG_PATH = web.AppKey("config_path", str)
SESSIONS = web.AppKey("sessions", set)
GUARD = web.AppKey("guard", object)


def _json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path in PUBLIC_PATHS:
        return await handler(request)
    if request.cookies.get(COOKIE) not in request.app[SESSIONS]:
        if request.path.startswith("/api/"):
            return _json_error("未登录", 401)
        raise web.HTTPFound("/login")
    if request.path.startswith("/api/"):
        # 只收 JSON：配合 SameSite cookie，挡住跨站表单提交
        if request.method == "POST" and request.content_type != "application/json":
            return _json_error("需要 application/json", 415)
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except ValueError as e:
            return _json_error(str(e))
        except Exception as e:
            logger.exception("接口出错：%s", request.path)
            return _json_error(str(e) or type(e).__name__, 500)
    return await handler(request)


# ---- 页面与登录 ----


async def login_page(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC / "login.html")


class LoginGuard:
    """输错密码后等待时间逐次翻倍；当天累计输错 MAX_FAILURES 次就停用到第二天 0 点。

    只记在内存里，重启程序会清零。
    """

    MAX_FAILURES = 7
    BASE_DELAY = 5  # 第一次输错后等 5 秒，之后每次翻倍

    def __init__(self) -> None:
        self.day = date.today()
        self.failures = 0
        self.next_allowed = 0.0

    def _roll_day(self) -> None:
        if date.today() != self.day:
            self.day = date.today()
            self.failures = 0
            self.next_allowed = 0.0

    def check(self) -> str | None:
        """不能尝试时返回跳转参数，可以尝试时返回 None。"""
        self._roll_day()
        if self.failures >= self.MAX_FAILURES:
            return "locked=1"
        wait = math.ceil(self.next_allowed - time.monotonic())
        return f"wait={wait}" if wait > 0 else None

    def fail(self) -> str:
        self.failures += 1
        if self.failures >= self.MAX_FAILURES:
            logger.warning("管理页面密码今天已输错 %s 次，停用到明天", self.failures)
            return "locked=1"
        delay = self.BASE_DELAY * 2 ** (self.failures - 1)
        self.next_allowed = time.monotonic() + delay
        return f"error=1&left={self.MAX_FAILURES - self.failures}&wait={delay}"


async def login_submit(request: web.Request) -> web.Response:
    guard = request.app[GUARD]
    if blocked := guard.check():
        raise web.HTTPFound(f"/login?{blocked}")
    form = await request.post()
    expected = request.app[RUNNER].config.web_password
    given = str(form.get("password", ""))
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise web.HTTPFound(f"/login?{guard.fail()}")
    token = secrets.token_urlsafe(32)
    request.app[SESSIONS].add(token)
    response = web.HTTPFound("/")
    response.set_cookie(
        COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="Strict"
    )
    raise response


async def logout(request: web.Request) -> web.Response:
    request.app[SESSIONS].discard(request.cookies.get(COOKIE))
    response = web.HTTPFound("/login")
    response.del_cookie(COOKIE)
    raise response


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC / "index.html")


# ---- 接口 ----


def _require_running(runner: Runner):
    if miboy := runner.active:
        return miboy
    raise ValueError("服务还没连上音箱，先完成小米登录")


async def status(request: web.Request) -> web.Response:
    runner = request.app[RUNNER]
    config = runner.config
    miboy = runner.miboy
    music = miboy.music if miboy and miboy.ready else None
    current = music.current if music else None
    return web.json_response(
        {
            "state": runner.state,
            "error": runner.error,
            "notification_url": runner.notification_url,
            "hardware": config.hardware,
            "mi_did": config.mi_did,
            "mi_device_id": config.mi_device_id,
            "model": (config.qwen_options or {}).get("model") or QwenBot.default_model,
            "songs": len(miboy.library.songs) if music else None,
            "music_state": music.state if music else None,
            "current": current.display if current else None,
            "current_id": current.id if current else None,
            "current_favorite": bool(current and current in miboy.favorites),
            "favorites": len(miboy.favorites.songs) if music else None,
        }
    )


async def get_settings(request: web.Request) -> web.Response:
    return web.json_response(settings.describe(request.app[RUNNER].config))


async def save_settings(request: web.Request) -> web.Response:
    runner = request.app[RUNNER]
    changed = settings.apply(runner.config, request.app[CONFIG_PATH], await request.json())
    restarted = False
    if any(f.restart for f in changed) or runner.state in ("waiting_config", "error"):
        await runner.restart()
        restarted = True
    elif runner.miboy and any(f.group == "千问" for f in changed):
        runner.miboy.__dict__.pop("chatbot", None)  # 下次对话按新配置重建
    return web.json_response(
        {"changed": [f.label for f in changed], "restarted": restarted}
    )


async def restart(request: web.Request) -> web.Response:
    await request.app[RUNNER].restart()
    return web.json_response({"ok": True})


async def mi_login(request: web.Request) -> web.Response:
    """单独登录一次小米：拿到验证链接，或者登录成功后列出音箱供选择。"""
    config = request.app[RUNNER].config
    if not (config.account and config.password):
        raise ValueError("先在设置里填小米账号和密码")
    device_id = resolve_device_id(config.mi_device_id, config.mi_token_path)
    if not config.mi_device_id:
        settings.apply(config, request.app[CONFIG_PATH], {"mi_device_id": device_id})
    try:
        async with ClientSession() as session:
            account = await login_mi(
                session, config.account, config.password, device_id, config.mi_token_path
            )
            devices = await MiNAService(account).device_list() or []
    except MiLoginError as e:
        return web.json_response(
            {"ok": False, "error": str(e), "notification_url": e.notification_url}
        )
    return web.json_response(
        {
            "ok": True,
            "devices": [
                {
                    "name": d.get("name", ""),
                    "hardware": d.get("hardware", ""),
                    "mi_did": str(d.get("miotDID", "")),
                }
                for d in devices
            ],
        }
    )


async def mi_device(request: web.Request) -> web.Response:
    runner = request.app[RUNNER]
    data = await request.json()
    settings.apply(
        runner.config,
        request.app[CONFIG_PATH],
        {"hardware": data.get("hardware", ""), "mi_did": data.get("mi_did", "")},
    )
    await runner.restart()
    return web.json_response({"ok": True})


async def test_chat(request: web.Request) -> web.Response:
    text = str((await request.json()).get("text", "")).strip()
    if not text:
        raise ValueError("请输入问题")
    config = request.app[RUNNER].config
    if not config.qwen_key:
        raise ValueError("先在设置里填千问 API key")
    answer = await asyncio.wait_for(QwenBot.from_config(config).ask(text), 60)
    return web.json_response({"answer": answer})


async def test_say(request: web.Request) -> web.Response:
    text = str((await request.json()).get("text", "")).strip()
    if not text:
        raise ValueError("请输入要播报的话")
    await _require_running(request.app[RUNNER]).do_tts(text)
    return web.json_response({"ok": True})


async def songs(request: web.Request) -> web.Response:
    miboy = _require_running(request.app[RUNNER])
    query = request.query.get("q", "").strip()
    if request.query.get("favorites"):
        found = miboy.favorites.songs
    else:
        found = miboy.music.find(query) if query else miboy.library.songs
    return web.json_response(
        [
            {
                "id": s.id,
                "display": s.display,
                "duration": s.duration,
                "favorite": s in miboy.favorites,
            }
            for s in found[:50]
        ]
    )


async def music_play(request: web.Request) -> web.Response:
    miboy = _require_running(request.app[RUNNER])
    data = await request.json()
    if data.get("favorites"):
        if not await miboy.play_favorites():
            raise ValueError("收藏里还没有歌")
        return web.json_response({"ok": True})
    song = miboy.library.get(str(data.get("id", "")))
    if song is None:
        raise ValueError("找不到这首歌")
    await miboy.music.play([song])
    return web.json_response({"ok": True})


async def favorite(request: web.Request) -> web.Response:
    miboy = _require_running(request.app[RUNNER])
    data = await request.json()
    song = miboy.library.get(str(data.get("id", "")))
    if song is None:
        raise ValueError("找不到这首歌")
    if data.get("favorite"):
        miboy.favorites.add(song)
    else:
        miboy.favorites.remove(song)
    return web.json_response({"ok": True, "favorite": song in miboy.favorites})


async def music_control(request: web.Request) -> web.Response:
    miboy = _require_running(request.app[RUNNER])
    action = str((await request.json()).get("action", ""))
    if action not in ("pause", "resume", "next", "previous", "stop"):
        raise ValueError("不支持的操作")
    await miboy.music.control(action)
    return web.json_response({"ok": True})


def create_app(runner: Runner, config_path: str) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app[RUNNER] = runner
    app[CONFIG_PATH] = config_path
    app[SESSIONS] = set()
    app[GUARD] = LoginGuard()
    app.add_routes(
        [
            web.get("/login", login_page),
            web.post("/login", login_submit),
            web.post("/logout", logout),
            web.get("/", index),
            web.get("/api/status", status),
            web.get("/api/settings", get_settings),
            web.post("/api/settings", save_settings),
            web.post("/api/restart", restart),
            web.post("/api/mi/login", mi_login),
            web.post("/api/mi/device", mi_device),
            web.post("/api/test/chat", test_chat),
            web.post("/api/test/say", test_say),
            web.get("/api/songs", songs),
            web.post("/api/music/play", music_play),
            web.post("/api/music/control", music_control),
            web.post("/api/favorites", favorite),
        ]
    )
    return app


async def start_web(runner: Runner, config_path: str, port: int) -> web.AppRunner:
    app_runner = web.AppRunner(create_app(runner, config_path), access_log=None)
    await app_runner.setup()
    await web.TCPSite(app_runner, port=port).start()
    logger.info("管理页面已启动：http://0.0.0.0:%s", port)
    return app_runner
