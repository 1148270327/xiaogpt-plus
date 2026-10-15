from __future__ import annotations

import hashlib
import logging
import random
import string

from aiohttp import ClientSession
from miservice import MiAccount
from miservice.miaccount import MiTokenStore

logger = logging.getLogger("xiaogpt.login")


class MiLoginError(Exception):
    def __init__(self, message: str, notification_url: str = "") -> None:
        super().__init__(message)
        self.notification_url = notification_url


def resolve_device_id(configured: str, token_path: str) -> str:
    """配置里有就用配置；否则沿用 token 文件里的；都没有就新生成一个。"""
    if configured:
        return configured
    token = MiTokenStore(token_path).load_token() or {}
    if device_id := token.get("deviceId"):
        return device_id
    device_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    logger.warning(
        "没有配置 mi_device_id，已生成：%s\n请写进配置文件，换机器部署时保持不变", device_id
    )
    return device_id


class FixedDeviceAccount(MiAccount):
    """固定 deviceId 的小米账号。

    小米按 deviceId 识别登录设备，新设备要先在浏览器完成一次安全验证。
    原库每次登录失败都会换一个随机 deviceId，验证永远过不去，这里始终用配置里的那个。
    """

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        device_id: str,
        token_path: str,
    ) -> None:
        super().__init__(session, username, password, token_path)
        self.device_id = device_id
        self.notification_url = ""
        self.error = ""
        if not self.token or self.token.get("deviceId") != device_id:
            self.token = {"deviceId": device_id}

    async def login(self, sid: str) -> bool:
        if not self.token:
            self.token = {"deviceId": self.device_id}
        self.notification_url = ""
        try:
            resp = await self._serviceLogin(f"serviceLogin?sid={sid}&_json=true")
            if resp["code"] != 0:
                resp = await self._serviceLogin(
                    "serviceLoginAuth2",
                    {
                        "_json": "true",
                        "qs": resp["qs"],
                        "sid": resp["sid"],
                        "_sign": resp["_sign"],
                        "callback": resp["callback"],
                        "user": self.username,
                        "hash": hashlib.md5(self.password.encode()).hexdigest().upper(),
                    },
                )
                if resp["code"] != 0:
                    raise MiLoginError(f"账号或密码错误：{resp.get('desc', resp)}")
            if "userId" not in resp:
                url = resp.get("notificationUrl", "")
                if url.startswith("/"):
                    url = "https://account.xiaomi.com" + url
                self.notification_url = url
                raise MiLoginError("小米要求这台设备先完成安全验证")

            self.token["userId"] = resp["userId"]
            self.token["passToken"] = resp["passToken"]
            service_token = await self._securityTokenService(
                resp["location"], resp["nonce"], resp["ssecurity"]
            )
            self.token[sid] = (resp["ssecurity"], service_token)
            self.token_store.save_token(self.token)
            return True
        except Exception as e:
            # 只清登录态，保留 deviceId，验证完成后用同一个 deviceId 重试才有效
            self.token = {"deviceId": self.device_id}
            self.token_store.save_token(self.token)
            self.error = str(e)
            logger.error("小米登录失败：%s", e)
            if self.notification_url:
                logger.error("请在浏览器打开下面的链接完成验证，然后重新运行：\n%s", self.notification_url)
            return False


async def login_mi(
    session: ClientSession,
    username: str,
    password: str,
    device_id: str,
    token_path: str,
) -> FixedDeviceAccount:
    account = FixedDeviceAccount(session, username, password, device_id, token_path)
    if not await account.login("micoapi"):
        raise MiLoginError(account.error or "小米登录失败", account.notification_url)
    return account
