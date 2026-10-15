from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from config import Config

_EOF = object()


class QwenBot:
    name = "千问"
    default_model = "qwen-turbo"
    max_turns = 5

    def __init__(
        self,
        api_key: str,
        prompt: str,
        options: dict[str, Any] | None = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ) -> None:
        self.api_key = api_key
        self.prompt = prompt
        self.options = dict(options or {})
        self.base_url = base_url.rstrip("/")
        self.history: list[dict[str, str]] = []

    @classmethod
    def from_config(cls, config: Config) -> QwenBot:
        return cls(
            config.qwen_key, config.prompt, config.qwen_options, config.qwen_base_url
        )

    def change_prompt(self, new_prompt: str) -> None:
        self.prompt = new_prompt

    def _messages(self, query: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.prompt},
            *self.history,
            {"role": "user", "content": query},
        ]

    def _remember(self, query: str, answer: str) -> None:
        self.history += [
            {"role": "user", "content": query},
            {"role": "assistant", "content": answer},
        ]
        self.history = self.history[-self.max_turns * 2 :]

    def _request(self, query: str, stream: bool) -> dict[str, Any]:
        options = {"model": self.default_model, **self.options}
        # 思考过程不拿去念。由 qwen_options.enable_thinking 控制，缺省关闭
        enable_thinking = bool(options.pop("enable_thinking", False))
        return {
            "messages": self._messages(query),
            "stream": stream,
            "extra_body": {"enable_thinking": enable_thinking},
            **options,
        }

    def _call(self, query: str, stream: bool):
        from openai import APIStatusError, OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        try:
            return client.chat.completions.create(**self._request(query, stream))
        except APIStatusError as e:
            raise self._error(e) from e

    @staticmethod
    def _error(exc) -> RuntimeError:
        detail = getattr(exc, "message", None) or str(exc)
        status = getattr(exc, "status_code", "")
        return RuntimeError(f"千问请求失败：{status} {detail}".strip())

    @staticmethod
    def _text(choice) -> str:
        message = getattr(choice, "message", None) or getattr(choice, "delta", None)
        return (getattr(message, "content", None) or "") if message else ""

    async def ask(self, query: str) -> str:
        response = await asyncio.to_thread(self._call, query, False)
        answer = self._text(response.choices[0])
        self._remember(query, answer)
        return answer

    async def ask_stream(self, query: str) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def produce() -> None:
            try:
                for chunk in self._call(query, True):
                    if not chunk.choices:
                        continue
                    if text := self._text(chunk.choices[0]):
                        loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as e:
                from openai import APIStatusError

                if isinstance(e, APIStatusError):
                    e = self._error(e)
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _EOF)

        producer = loop.run_in_executor(None, produce)
        answer = ""
        while (chunk := await queue.get()) is not _EOF:
            if isinstance(chunk, Exception):
                raise chunk
            answer += chunk
            yield chunk
        await producer
        self._remember(query, answer)
