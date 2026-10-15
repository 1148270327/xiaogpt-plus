#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import random
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from aiohttp import ClientSession, ClientTimeout
from miservice import MiIOService, MiNAService, miio_command
from rich import print
from rich.logging import RichHandler

from config import COOKIE_TEMPLATE, LATEST_ASK_API, WAKEUP_KEYWORD, Config
from intent import Intent, Kind, route
from mi_login import login_mi, resolve_device_id
from player import Favorites, MusicLibrary, MusicPlayer, MusicServer
from qwen_bot import QwenBot
from tts import TTS, MiTTS, TetosFileTTS
from tts.live import TetosLiveTTS
from utils import (
    calculate_tts_elapse,
    detect_language,
    get_hostname,
    parse_cookie_string,
    split_speakable,
)

EOF = object()
MAX_INFLIGHT_POLLS = 3
MAX_POLL_BACKOFF = 30  # 查询连续失败时间隔依次 1、2、4…秒，最多 30 秒
RELOGIN_AFTER_FAILURES = 3
RELOGIN_MIN_INTERVAL = 600
MUTE_CHECK_INTERVAL = 0.3
MAX_FILLERS = 2
# 发出播报命令到音箱真正出声的大致耗时
TTS_START_DELAY = 0.5


class MiGPT:
    def __init__(self, config: Config):
        self.config = config
        if config.music_http_host:
            # tts/ 里的临时音频服务也靠 get_hostname() 拼地址，和音乐用同一个 IP
            os.environ["XIAOGPT_HOSTNAME"] = config.music_http_host

        self.mi_token_home = Path(config.mi_token_path)
        self.last_timestamp = int(time.time() * 1000)  # timestamp last call mi speaker
        self.cookie_jar = None
        self.device_id = ""
        self.mina_service = None
        self.miio_service = None
        self.in_conversation = False
        self.polling_event = asyncio.Event()
        self.last_record = asyncio.Queue(1)
        self.library = MusicLibrary(config.music_dir)
        self.favorites = Favorites(config.favorites_path, self.library)
        self.music_server: MusicServer | None = None
        self.music: MusicPlayer | None = None
        self.ready = False
        self._reinit_lock = asyncio.Lock()
        self._last_relogin = -RELOGIN_MIN_INTERVAL
        self._poll_failures = 0
        self._last_activity = time.monotonic()
        self._activity = asyncio.Event()
        self._filler_until = 0.0
        # setup logger
        self.log = logging.getLogger("xiaogpt")
        self.log.setLevel(logging.DEBUG if config.verbose else logging.INFO)
        if not self.log.handlers:
            self.log.addHandler(RichHandler())
        self.log.debug(config)
        self.mi_session = ClientSession()

    async def close(self):
        self.ready = False
        if self.music:
            self.music.close()
        if self.music_server:
            self.music_server.stop()
        if "tts" in self.__dict__:
            self.tts.close()
        await self.mi_session.close()

    async def poll_latest_ask(self):
        # 对话记录接口一次要几百毫秒到一秒多。按固定节奏发请求、不等上一个返回，
        # 发现新记录的延迟就只取决于接口耗时，不再是「接口耗时 + 间隔」
        inflight: set[asyncio.Task] = set()
        async with ClientSession() as session:
            session._cookie_jar = self.cookie_jar
            log_polling = int(self.config.verbose) > 1
            idle = False
            try:
                while True:
                    await self.polling_event.wait()
                    if idle != (idle := self._is_idle()):
                        self.log.info(
                            "%s，每 %.1f 秒查一次对话记录",
                            "一段时间没人说话，降低查询频率" if idle else "有人说话，恢复快速查询",
                            self._poll_interval_now(),
                        )
                    # 出错时只留一个请求在途，避免被限流时还在并发猛查
                    max_inflight = 1 if self._poll_failures else MAX_INFLIGHT_POLLS
                    if len(inflight) < max_inflight:
                        if log_polling:
                            self.log.debug(
                                "Listening new message, timestamp: %s",
                                self.last_timestamp,
                            )
                        task = asyncio.create_task(
                            self.get_latest_ask_from_xiaoai(session)
                        )
                        inflight.add(task)
                        task.add_done_callback(inflight.discard)
                    # 闲置时睡得久，查到新对话要立刻醒来切回快查
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(
                            self._activity.wait(), self._poll_interval_now()
                        )
                    self._activity.clear()
            finally:
                for task in inflight:
                    task.cancel()

    def _in_night_hours(self) -> bool:
        start, end = self.config.night_start, self.config.night_end
        hour = datetime.now().hour
        if start < end:
            return start <= hour < end
        if start > end:  # 跨午夜，比如 23 点到 7 点
            return hour >= start or hour < end
        return False

    def _is_idle(self) -> bool:
        return time.monotonic() - self._last_activity > self.config.idle_after

    def _poll_interval_now(self) -> float:
        interval = self.config.poll_interval
        if self._is_idle():
            interval = max(interval, self.config.idle_poll_interval)
        if self._in_night_hours():
            interval = max(interval, self.config.night_poll_interval)
        if self._poll_failures:
            backoff = min(2 ** (self._poll_failures - 1), MAX_POLL_BACKOFF)
            interval = max(interval, backoff)
        return interval

    async def init_all_data(self):
        await self.login_miboy()
        await self._init_data_hardware()
        self.mi_session.cookie_jar.update_cookies(self.get_cookie())
        self.cookie_jar = self.mi_session.cookie_jar
        self.tts  # init tts
        self._init_music()

    def _init_music(self):
        if self.music is not None:
            self.music.bind(self.mina_service, self.device_id)
            return
        self.library.scan()
        token = self.config.music_http_token or secrets.token_urlsafe(18)
        self.music_server = MusicServer(self.library, self.config.music_http_port, token)
        self.music_server.start()
        host = self.config.music_http_host or get_hostname()
        self.music = MusicPlayer(
            self.library,
            f"http://{host}:{self.music_server.port}",
            self.mina_service,
            self.device_id,
            token,
        )

    async def login_miboy(self):
        token_path = str(self.mi_token_home)
        account = await login_mi(
            self.mi_session,
            self.config.account,
            self.config.password,
            resolve_device_id(self.config.mi_device_id, token_path),
            token_path,
        )
        self.mina_service = MiNAService(account)
        self.miio_service = MiIOService(account)

    async def _init_data_hardware(self):
        if self.config.cookie:
            # if use cookie do not need init
            return
        hardware_data = await self.mina_service.device_list()
        # fix multi xiaoai problems we check did first
        # why we use this way to fix?
        # some videos and articles already in the Internet
        # we do not want to change old way, so we check if miotDID in `env` first
        # to set device id

        for h in hardware_data:
            if did := self.config.mi_did:
                if h.get("miotDID", "") == str(did):
                    self.device_id = h.get("deviceID")
                    break
                else:
                    continue
            if h.get("hardware", "") == self.config.hardware:
                self.device_id = h.get("deviceID")
                break
        else:
            raise Exception(
                f"we have no hardware: {self.config.hardware} please use `python list_mi_devices.py` to check"
            )
        if not self.config.mi_did:
            devices = await self.miio_service.device_list()
            try:
                self.config.mi_did = next(
                    d["did"]
                    for d in devices
                    if d["model"].endswith(self.config.hardware.lower())
                )
            except StopIteration:
                raise Exception(
                    f"cannot find did for hardware: {self.config.hardware} "
                    "please set it via MI_DID env"
                )

    def get_cookie(self):
        if self.config.cookie:
            cookie_jar = parse_cookie_string(self.config.cookie)
            # set attr from cookie fix #134
            cookie_dict = cookie_jar.get_dict()
            self.device_id = cookie_dict["deviceId"]
            return cookie_jar
        else:
            with open(self.mi_token_home) as f:
                user_data = json.loads(f.read())
            user_id = user_data.get("userId")
            service_token = user_data.get("micoapi")[1]
            cookie_string = COOKIE_TEMPLATE.format(
                device_id=self.device_id, service_token=service_token, user_id=user_id
            )
            return parse_cookie_string(cookie_string)

    @functools.cached_property
    def chatbot(self) -> QwenBot:
        return QwenBot.from_config(self.config)

    def intent_of(self, record: dict) -> Intent:
        return route(record.get("query", ""), self.config.xiaoai_keywords)

    def need_change_prompt(self, record):
        query = record.get("query", "")
        return query.startswith(tuple(self.config.change_prompt_keyword))

    def _change_prompt(self, new_prompt):
        new_prompt = re.sub(
            rf"^({'|'.join(self.config.change_prompt_keyword)})", "", new_prompt
        )
        new_prompt = "以下都" + new_prompt
        print(f"Prompt from {self.config.prompt} change to {new_prompt}")
        self.config.prompt = new_prompt
        self.chatbot.change_prompt(new_prompt)

    async def get_latest_ask_from_xiaoai(self, session: ClientSession) -> dict | None:
        """查一次对话记录。轮询本身会不停地查，这里失败了不原地重试，交给轮询退避。"""
        try:
            r = await session.get(
                LATEST_ASK_API.format(
                    hardware=self.config.hardware,
                    timestamp=str(int(time.time() * 1000)),
                ),
                timeout=ClientTimeout(total=15),
            )
        except Exception as e:
            self._poll_failed(f"网络异常：{e}")
            return None
        try:
            data = await r.json()
            if data.get("code", 0) != 0:
                raise ValueError(data.get("message"))
        except Exception as e:
            # 多半是登录凭证过期或被限流
            self._poll_failed(f"返回异常（HTTP {r.status}）：{e}")
            if self._poll_failures >= RELOGIN_AFTER_FAILURES:
                await self._retry()
            return None
        if self._poll_failures:
            self.log.info("对话记录恢复正常，轮询回到正常频率")
            self._poll_failures = 0
        return self._get_last_query(data)

    def _poll_failed(self, reason: str) -> None:
        self._poll_failures += 1
        self.log.warning(
            "查询对话记录失败（连续 %s 次）：%s，%.0f 秒后再查",
            self._poll_failures,
            reason,
            self._poll_interval_now(),
        )

    async def _retry(self):
        # 频繁重新登录最容易触发小米风控：并发失败时只让一个去登录，且有最短间隔
        if self._reinit_lock.locked():
            return
        if (wait := self._last_relogin + RELOGIN_MIN_INTERVAL - time.monotonic()) > 0:
            self.log.warning(
                "距上次重新登录不到 %d 分钟，%.0f 秒后才会再试",
                RELOGIN_MIN_INTERVAL // 60,
                wait,
            )
            return
        async with self._reinit_lock:
            self._last_relogin = time.monotonic()
            print("Maybe outof date trying to re init it")
            try:
                await self.init_all_data()
            except Exception as e:
                self.log.error("重新登录小米失败：%s", e)

    def _get_last_query(self, data: dict) -> dict | None:
        if d := data.get("data"):
            records = json.loads(d).get("records")
            if not records:
                return None
            last_record = records[0]
            timestamp = last_record.get("time")
            if timestamp > self.last_timestamp:
                self._last_activity = time.monotonic()
                self._activity.set()
                try:
                    self.last_record.put_nowait(last_record)
                    self.last_timestamp = timestamp
                    return last_record
                except asyncio.QueueFull:
                    pass
        return None

    async def do_tts(self, value):
        if not self.config.use_command:
            try:
                await self.mina_service.text_to_speech(self.device_id, value)
            except Exception:
                pass
        else:
            await miio_command(
                self.miio_service,
                self.config.mi_did,
                f"{self.config.tts_command} {value}",
            )

    async def say(self, text: str) -> None:
        """用小爱原声说一句短提示，并等它说完。"""
        await self.do_tts(text)
        await asyncio.sleep(calculate_tts_elapse(text))

    @functools.cached_property
    def tts(self) -> TTS:
        if self.config.tts == "mi":
            return MiTTS(self.mina_service, self.device_id, self.config)
        elif self.config.tts == "fish":
            return TetosLiveTTS(self.mina_service, self.device_id, self.config)
        else:
            return TetosFileTTS(self.mina_service, self.device_id, self.config)

    @staticmethod
    def _normalize(message: str) -> str:
        message = message.strip().replace(" ", "--")
        message = message.replace("\n", "，")
        message = message.replace('"', "，")
        message = message.replace("*", "")
        return message

    async def ask_gpt(self, query: str) -> AsyncIterator[str]:
        if not self.config.stream:
            answer = await self.chatbot.ask(query)
            print(answer)
            yield self._normalize(answer) if answer else ""
            return

        async def collect_stream(queue):
            async for message in self.chatbot.ask_stream(query):
                await queue.put(message)

        def done_callback(future):
            queue.put_nowait(EOF)
            if not future.cancelled() and future.exception():
                self.log.error(future.exception())

        queue = asyncio.Queue()
        is_eof = False
        buffer = ""
        task = asyncio.create_task(collect_stream(queue))
        task.add_done_callback(done_callback)
        try:
            while not is_eof and self.last_record.empty():
                message = await queue.get()
                if message is EOF:
                    break
                buffer += message
                while not queue.empty():
                    if (next_msg := queue.get_nowait()) is EOF:
                        is_eof = True
                        break
                    buffer += next_msg
                segment, buffer = split_speakable(buffer)
                if segment.strip():
                    print(segment, end="")
                    yield self._normalize(segment)
            if buffer.strip() and self.last_record.empty():
                print(buffer, end="")
                yield self._normalize(buffer)
        finally:
            task.cancel()

    async def get_if_xiaoai_is_playing(self):
        playing_info = await self.mina_service.player_get_status(self.device_id)
        # WTF xiaomi api
        is_playing = (
            json.loads(playing_info.get("data", {}).get("info", "{}")).get("status", -1)
            == 1
        )
        return is_playing

    async def _xiaoai_is_playing(self) -> bool:
        try:
            return await self.get_if_xiaoai_is_playing()
        except Exception as e:
            self.log.warning("查询小爱播放状态失败：%s", e)
            return False

    async def stop_if_xiaoai_is_playing(self):
        if await self._xiaoai_is_playing():
            self.log.debug("Muting xiaoai")
            await self.pause_xiaoai()

    async def wakeup_xiaoai(self):
        return await miio_command(
            self.miio_service,
            self.config.mi_did,
            f"{self.config.wakeup_command} {WAKEUP_KEYWORD} 0",
        )

    async def pause_xiaoai(self):
        try:
            await self.mina_service.player_pause(self.device_id)
        except Exception as e:
            self.log.warning("打断小爱失败：%s", e)

    async def _quiet_xiaoai(
        self, filler: str = "", ready: asyncio.Future | None = None
    ) -> None:
        """压住小爱自己的回答。

        有垫话时立刻念垫话：新的播报会打断正在播的声音，小爱刚开口的半句就被换成垫话。
        小爱也可能在垫话之后才开口，所以之后继续检查，垫话念完还在出声的就是小爱，
        暂停它；千问还没准备好就再补一句垫话。没有垫话时只做暂停。
        """
        if not self.config.mute_xiaoai:
            # waiting for xiaoai speaker done
            await asyncio.sleep(8)
            return
        start = time.monotonic()
        fillers_left = MAX_FILLERS if filler else 0
        if fillers_left:
            await self.say_filler(filler)
            fillers_left -= 1
        else:
            await self.pause_xiaoai()
        while True:
            deadline = max(start + self.config.mute_window, self._filler_until + 0.6)
            if (left := deadline - time.monotonic()) <= 0:
                break
            await asyncio.sleep(min(MUTE_CHECK_INTERVAL, left))
            if time.monotonic() < self._filler_until:
                continue  # 自己的垫话还在念，这时查到「正在播放」是自己
            if not await self._xiaoai_is_playing():
                continue
            self.log.debug("Muting xiaoai")
            await self.pause_xiaoai()
            if fillers_left and not (ready and ready.done()):
                await self.say_filler(filler)
                fillers_left -= 1

    async def say_filler(self, filler: str) -> None:
        await self.do_tts(filler)
        self._filler_until = (
            time.monotonic() + TTS_START_DELAY + calculate_tts_elapse(filler)
        )

    async def run_forever(self):
        await self.init_all_data()
        poll_task = asyncio.create_task(self.poll_latest_ask())
        self.ready = True
        print(
            f"Running xiaogpt now，日常对话交给{self.chatbot.name}，"
            f"本地曲库 {len(self.library.songs)} 首"
        )
        print(f"用 [green]{self.config.start_conversation}[/] 开始持续对话")
        try:
            await self._handle_records()
        finally:
            poll_task.cancel()

    async def _handle_records(self):
        # 处理过程中也一直轮询：播报时能发现新的问题并打断
        self.polling_event.set()
        while True:
            new_record = await self.last_record.get()
            query = new_record.get("query", "").strip()
            if query == self.config.start_conversation:
                if not self.in_conversation:
                    print("开始对话")
                    self.in_conversation = True
                    await self.wakeup_xiaoai()
                await self.stop_if_xiaoai_is_playing()
                continue
            elif query == self.config.end_conversation:
                if self.in_conversation:
                    print("结束对话")
                    self.in_conversation = False
                await self.stop_if_xiaoai_is_playing()
                continue

            if self.need_change_prompt(new_record):
                self._change_prompt(query)
                await self.say("提示词已更改")
                continue

            intent = self.intent_of(new_record)
            if intent.kind is Kind.IGNORE:
                continue
            print("-" * 20)
            lag = time.time() - new_record.get("time", 0) / 1000
            print(f"[{intent.kind.value}] {query}（小爱记录后 {lag:.1f} 秒拿到）")
            try:
                if intent.kind is Kind.XIAOAI:
                    self.log.debug("交给小爱处理：%s", query)
                elif intent.kind is Kind.CONTROL:
                    await self.music.control(intent.arg)
                elif intent.kind is Kind.FAVORITE:
                    await self.handle_favorite(intent.arg)
                elif intent.kind is Kind.PLAY:
                    await self.play_music(intent.arg)
                else:
                    await self.chat(query)
            except Exception as e:
                self.log.exception("处理「%s」出错：%s", query, e)

    async def play_music(self, query: str) -> None:
        await self._quiet_xiaoai()
        songs = self.music.find(query)
        if not songs:
            await self.say(f"本地没有找到{query}" if query else "本地曲库是空的")
            return
        await self.say(f"为你播放{songs[0].display}")
        await self.music.play(songs, loop=not query)

    async def play_favorites(self) -> int:
        """打乱后循环播放收藏，返回首数；收藏为空返回 0。"""
        songs = self.favorites.songs
        if songs:
            random.shuffle(songs)
            await self.music.play(songs, loop=True)
        return len(songs)

    async def handle_favorite(self, action: str) -> None:
        if action == "play":
            await self._quiet_xiaoai()
            if not self.favorites.songs:
                await self.say("收藏里还没有歌，听到喜欢的歌时说收藏这首歌就行")
                return
            await self.say(f"播放你的收藏，共{len(self.favorites.songs)}首")
            await self.play_favorites()
            return

        song = self.music.current
        if song is None:
            await self._quiet_xiaoai()
            await self.say("现在没有在放本地音乐")
            return
        # 唤醒小爱时音箱会自己暂停音乐，说完确认要接着放
        was_playing = self.music.state == "playing"
        if action == "add":
            done = self.favorites.add(song)
            text = f"已收藏{song.display}" if done else f"{song.display}已经在收藏里了"
        else:
            done = self.favorites.remove(song)
            text = f"已把{song.display}移出收藏" if done else f"{song.display}不在收藏里"
        print(text)
        await self.say(text)
        await asyncio.sleep(TTS_START_DELAY)
        if was_playing and not await self._xiaoai_is_playing():
            await self.music.control("resume")

    async def chat(self, query: str) -> None:
        print("问题：" + query + "？")
        print(f"以下是 {self.chatbot.name} 的回答：", end="")
        stream = self.ask_gpt(query)
        # 千问的请求和压制小爱同时进行，不白等
        first = asyncio.ensure_future(stream.__anext__())
        interrupted = False
        try:
            await self._quiet_xiaoai(self.config.takeover_filler, first)
            interrupted = await self.speak(await first, stream)
        except StopAsyncIteration:
            interrupted = True
            print("（还没开口就有新的问题，跳过这次回答）")
        except Exception as e:
            print(f"{self.chatbot.name} 回答出错 {str(e)}")
            await self.say(f"{self.chatbot.name}出错了，请稍后再试")
        else:
            print("（被新的问题打断）" if interrupted else "回答完毕")
        finally:
            if not first.done():
                first.cancel()
                with contextlib.suppress(BaseException):
                    await first
            await stream.aclose()
        if self.in_conversation and not interrupted:
            print(f"继续对话，或用 `{self.config.end_conversation}` 结束对话")
            await self.wakeup_xiaoai()

    async def speak(self, first_chunk: str, text_stream: AsyncIterator[str]) -> bool:
        """播报回答。播报过程中有新的问题就立刻停下，返回 True。"""
        # 新的播报会顶掉正在念的垫话，等它念完再开口
        if (wait := self._filler_until - time.monotonic()) > 0:
            await asyncio.sleep(wait)
        # Detect the language from the first chunk
        # Add suffix '-' because tetos expects it to exist when selecting voices
        # however, the nation code is never used.
        lang = detect_language(first_chunk) + "-"

        async def gen():  # reconstruct the generator
            yield first_chunk
            async for text in text_stream:
                yield text

        synth = asyncio.create_task(self.tts.synthesize(lang, gen()))
        try:
            while not synth.done():
                await asyncio.wait({synth}, timeout=0.2)
                if not synth.done() and not self.last_record.empty():
                    return True
            synth.result()
            return False
        finally:
            if not synth.done():
                synth.cancel()
                with contextlib.suppress(BaseException):
                    await synth
