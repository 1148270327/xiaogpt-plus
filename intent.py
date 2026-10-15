from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class Kind(Enum):
    IGNORE = "ignore"
    CONTROL = "control"
    FAVORITE = "favorite"
    PLAY = "play"
    XIAOAI = "xiaoai"
    CHAT = "chat"


@dataclass(frozen=True)
class Intent:
    kind: Kind
    arg: str = ""


WAKEUP_PREFIX = "小爱同学"

CONTROL_WORDS = {
    "pause": ("暂停", "暂停播放", "暂停一下", "停一下", "先停一下", "暂停音乐"),
    "resume": ("继续", "继续播放", "接着放", "接着播放", "继续放", "恢复播放"),
    "stop": ("停止", "停止播放", "别放了", "不听了", "关掉音乐", "关闭音乐", "停止音乐", "退出", "退出播放"),
    "next": ("下一首", "下一曲", "换一首", "切歌", "换首歌", "换一首歌"),
    "previous": ("上一首", "上一曲"),
}
_CONTROL_LOOKUP = {word: action for action, words in CONTROL_WORDS.items() for word in words}

_THIS_SONG = r"(?:这首歌|这首|这个歌|这一首|当前歌曲|正在放的歌|这歌)?"
_FAVORITE_PATTERNS = (
    ("add", re.compile(rf"^(?:帮我|给我)?(?:收藏|加入收藏|添加到收藏|喜欢|我喜欢|把)?{_THIS_SONG}(?:加入收藏|加到收藏|添加到收藏|收藏起来|收藏|我喜欢)?$")),
    ("remove", re.compile(rf"^(?:帮我|给我)?(?:取消收藏|移出收藏|从收藏(?:里|中)?(?:移除|删除|删掉|去掉)|不喜欢|我不喜欢|把)?{_THIS_SONG}(?:取消收藏|移出收藏|从收藏(?:里|中)?(?:移除|删除|删掉|去掉))?$")),
    ("play", re.compile(r"^(?:请|帮我|给我)?(?:循环|随机)?(?:播放|放|听|打开|来点)(?:一下)?(?:我的|我)?(?:收藏|收藏的歌|收藏的歌曲|收藏的音乐|收藏歌单|喜欢的歌|喜欢的音乐|收藏夹)$")),
)
# 上面的模式各段都可省略，必须真的出现收藏 / 喜欢之类的词才算
_FAVORITE_WORDS = ("收藏", "喜欢")


def _favorite_action(query: str) -> str | None:
    if not any(word in query for word in _FAVORITE_WORDS):
        return None
    for action, pattern in _FAVORITE_PATTERNS:
        if pattern.match(query):
            return action
    return None

_POLITE = r"(?:请|帮我|给我|麻烦)?(?:随便|随机)?"
_PLAY_PATTERNS = (
    re.compile(
        rf"^{_POLITE}(?:播放|放一首|放首|放点|放一下|来一首|来首|来点|播一首|播首|我想听|我要听|想听|听一首|听首)(.*)$"
    ),
    re.compile(rf"^{_POLITE}放(.+?)的(?:歌|歌曲|音乐)$"),
    re.compile(rf"^{_POLITE}(?:打开|放)(音乐|歌|本地音乐)$"),
)
_PLAY_ARG_PREFIX = re.compile(r"^(?:一首|一下|首)")
_PLAY_ARG_SUFFIX = re.compile(r"(?:的歌曲|的歌|的音乐|这首歌|吧|呗|一下)$")
_RANDOM_ARGS = {"", "歌", "歌曲", "音乐", "一首歌", "首歌", "点歌", "点音乐", "随便", "随便一首", "本地音乐", "我的音乐"}

_DEVICES = r"(?:灯|空调|窗帘|电视(?!剧)|风扇|插座|扫地机|加湿器|净化器|热水器)"
_DEVICE_ACTIONS = r"(?:打开|关闭|关掉|关上|开启|调到|调高|调低|调成|设置|设为|设成|开|关|调)"
_DEVICE_CONTROL = re.compile(
    rf"{_DEVICE_ACTIONS}.{{0,6}}{_DEVICES}|{_DEVICES}.{{0,6}}{_DEVICE_ACTIONS}"
)

_PUNCTUATION = re.compile(r"[\s。，、！？!?,.；;：:\"'“”‘’]+")


def normalize_query(query: str) -> str:
    query = _PUNCTUATION.sub("", query)
    while query.startswith(WAKEUP_PREFIX):
        query = query[len(WAKEUP_PREFIX) :]
    return query


def _play_arg(query: str) -> str | None:
    for pattern in _PLAY_PATTERNS:
        if match := pattern.match(query):
            arg = _PLAY_ARG_PREFIX.sub("", match.group(1))
            arg = _PLAY_ARG_SUFFIX.sub("", arg)
            return "" if arg in _RANDOM_ARGS or arg.startswith("随便") else arg
    return None


def route(query: str, xiaoai_keywords: Iterable[str]) -> Intent:
    query = normalize_query(query)
    if not query:
        return Intent(Kind.IGNORE)
    if action := _CONTROL_LOOKUP.get(query):
        return Intent(Kind.CONTROL, action)
    if action := _favorite_action(query):
        return Intent(Kind.FAVORITE, action)
    if (arg := _play_arg(query)) is not None:
        return Intent(Kind.PLAY, arg)
    if _DEVICE_CONTROL.search(query) or any(
        word and word in query for word in xiaoai_keywords
    ):
        return Intent(Kind.XIAOAI)
    return Intent(Kind.CHAT, query)
