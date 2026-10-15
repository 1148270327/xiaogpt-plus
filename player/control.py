from __future__ import annotations

import asyncio
import json
import logging
import random

from miservice import MiNAService

from player.index import MusicLibrary, Song
from player.match import search

logger = logging.getLogger("xiaogpt.player")

RESCAN_AFTER_SECONDS = 60
WATCH_INTERVAL = 2.0
# 音箱状态变成「未播放」超过这么久、且歌还没播完，就当作被外部停掉，不再自动切歌
INTERRUPT_SECONDS = 15.0


class MusicPlayer:
    def __init__(
        self,
        library: MusicLibrary,
        base_url: str,
        mina_service: MiNAService,
        device_id: str,
    ) -> None:
        self.library = library
        self.base_url = base_url.rstrip("/")
        self.mina_service = mina_service
        self.device_id = device_id
        self.queue: list[Song] = []
        self.index = 0
        self.loop = False
        self.state = "stopped"  # playing / paused / stopped
        self._played = 0.0
        self._watch_task: asyncio.Task | None = None

    def close(self) -> None:
        self._stop_watch()

    def bind(self, mina_service: MiNAService, device_id: str) -> None:
        self.mina_service = mina_service
        self.device_id = device_id

    @property
    def current(self) -> Song | None:
        return self.queue[self.index] if self.queue else None

    def url_for(self, song: Song) -> str:
        return f"{self.base_url}/song/{song.id}{song.path.suffix.lower()}"

    def find(self, query: str) -> list[Song]:
        """空查询返回打乱的整个曲库；否则按匹配度排序。"""
        if not query:
            songs = self.library.songs
            random.shuffle(songs)
            return songs
        songs = search(self.library.songs, query)
        if not songs and self.library.is_stale(RESCAN_AFTER_SECONDS):
            self.library.scan()
            songs = search(self.library.songs, query)
        return songs

    async def play(self, songs: list[Song], loop: bool = False) -> None:
        self.queue = songs
        self.index = 0
        self.loop = loop
        await self._play_current()

    async def control(self, action: str) -> None:
        if action == "pause":
            await self.mina_service.player_pause(self.device_id)
            if self.state == "playing":
                self.state = "paused"
        elif action == "resume":
            await self.mina_service.player_play(self.device_id)
            if self.queue:
                self.state = "playing"
                self._start_watch()
        elif action == "stop":
            await self.mina_service.player_stop(self.device_id)
            self._stop_watch()
            self.state = "stopped"
            self.queue = []
        elif action in ("next", "previous") and self.queue:
            step = 1 if action == "next" else -1
            self.index = (self.index + step) % len(self.queue)
            await self._play_current()

    async def _play_current(self) -> None:
        song = self.current
        if song is None:
            return
        url = self.url_for(song)
        logger.info("播放 %s：%s", song.display, url)
        await self.mina_service.play_by_url(self.device_id, url, _type=1)
        self.state = "playing"
        self._played = 0.0
        self._start_watch()

    async def _advance(self) -> None:
        if self.index + 1 < len(self.queue):
            self.index += 1
        elif self.loop and self.queue:
            self.index = 0
        else:
            self.state = "stopped"
            logger.info("播放队列已结束")
            return
        await self._play_current()

    def _start_watch(self) -> None:
        self._stop_watch()
        self._watch_task = asyncio.create_task(self._watch())

    def _stop_watch(self) -> None:
        task = self._watch_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._watch_task = None

    async def _is_playing(self) -> bool:
        info = await self.mina_service.player_get_status(self.device_id)
        status = json.loads(info.get("data", {}).get("info", "{}")).get("status", -1)
        return status == 1

    async def _watch(self) -> None:
        song = self.current
        idle = 0.0
        try:
            while self.state != "stopped" and song is self.current:
                await asyncio.sleep(WATCH_INTERVAL)
                if self.state == "paused":
                    continue
                if await self._is_playing():
                    self._played += WATCH_INTERVAL
                    idle = 0.0
                    continue
                idle += WATCH_INTERVAL
                if song.duration:
                    finished = self._played >= song.duration - 2 * WATCH_INTERVAL
                else:
                    finished = self._played > 0 and idle >= 2 * WATCH_INTERVAL
                if finished:
                    await self._advance()
                    return
                if idle >= INTERRUPT_SECONDS:
                    logger.info("%s 被外部中断，停止自动切歌", song.display)
                    self.state = "paused"
                    return
        except Exception:
            logger.exception("播放状态检查失败")
