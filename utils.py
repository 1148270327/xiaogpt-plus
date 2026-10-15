#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import socket
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

from requests.utils import cookiejar_from_dict

if TYPE_CHECKING:
    from lingua import LanguageDetector


### HELP FUNCTION ###
def parse_cookie_string(cookie_string):
    cookie = SimpleCookie()
    cookie.load(cookie_string)
    cookies_dict = {k: m.value for k, m in cookie.items()}
    return cookiejar_from_dict(cookies_dict, cookiejar=None, overwrite=True)


_no_elapse_chars = re.compile(r"([「」『』《》“”'\"()（）]|(?<!-)-(?!-))", re.UNICODE)


def calculate_tts_elapse(text: str) -> float:
    # for simplicity, we use a fixed speed
    speed = 4.5  # this value is picked by trial and error
    # Exclude quotes and brackets that do not affect the total elapsed time
    return len(_no_elapse_chars.sub("", text)) / speed


_SENTENCE_END = re.compile(r"[。！？!?；;\n]")
_SOFT_BREAK = re.compile(r"[，,、：:]")
MIN_SEGMENT = 8
MAX_SEGMENT = 120


def split_speakable(text: str) -> tuple[str, str]:
    """从流式文本里切出可以整段播报的部分，返回 (要念的, 剩下的)。

    每次播报音箱都要重新合成语音，段和段之间会停顿；在半句话处断开听起来像卡住。
    所以只在句末切，并且尽量把已到达的整句拼成一段。
    """
    cut = 0
    for match in _SENTENCE_END.finditer(text):
        if match.end() >= MIN_SEGMENT:
            cut = match.end()
    if not cut and len(text) > MAX_SEGMENT:
        soft = [m.end() for m in _SOFT_BREAK.finditer(text, 0, MAX_SEGMENT)]
        cut = soft[-1] if soft else MAX_SEGMENT
    return text[:cut], text[cut:]


def get_hostname() -> str:
    if "XIAOGPT_HOSTNAME" in os.environ:
        return os.environ["XIAOGPT_HOSTNAME"]

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def _get_detector() -> LanguageDetector | None:
    try:
        from lingua import LanguageDetectorBuilder
    except ImportError:
        return None
    return LanguageDetectorBuilder.from_all_spoken_languages().build()


_detector = _get_detector()


def detect_language(text: str) -> str:
    if _detector is None:
        return "zh"  # default to Chinese if langdetect module is not available
    lang = _detector.detect_language_of(text)
    return lang.iso_code_639_1.name.lower() if lang is not None else "zh"
