from __future__ import annotations

import asyncio
import contextlib
import logging

from config import Config
from mi_gpt import MiGPT
from mi_login import MiLoginError

logger = logging.getLogger("xiaogpt.app")


class Runner:
    """管理小爱主程序的启动、停止和重启，配置不全或登录失败时不退出，等网页修好再启动。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.miboy: MiGPT | None = None
        self.error = ""
        self.notification_url = ""
        self._state = "stopped"
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """waiting_config / starting / running / error / stopped"""
        if self._state == "starting" and self.miboy and self.miboy.ready:
            return "running"
        return self._state

    @property
    def active(self) -> MiGPT | None:
        """已连上音箱、可以下发指令的主程序。"""
        return self.miboy if self.state == "running" else None

    async def start(self) -> None:
        async with self._lock:
            await self._stop()
            self.error = ""
            self.notification_url = ""
            if missing := self.config.missing():
                self._state = "waiting_config"
                self.error = "缺少配置：" + "、".join(missing)
                return
            self._state = "starting"
            self.miboy = MiGPT(self.config)
            self._task = asyncio.create_task(self._run(self.miboy))

    restart = start

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()
            self._state = "stopped"

    async def _stop(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.miboy = None

    async def _run(self, miboy: MiGPT) -> None:
        try:
            await miboy.run_forever()
        except asyncio.CancelledError:
            raise
        except MiLoginError as e:
            self._state = "error"
            self.error = f"小米登录失败：{e}"
            self.notification_url = e.notification_url
        except Exception as e:
            logger.exception("主程序异常退出")
            self._state = "error"
            self.error = str(e) or type(e).__name__
        finally:
            await miboy.close()
