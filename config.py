from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

LATEST_ASK_API = "https://userprofile.mina.mi.com/device_profile/v2/conversation?source=dialogu&hardware={hardware}&timestamp={timestamp}&limit=2"
COOKIE_TEMPLATE = "deviceId={device_id}; serviceToken={service_token}; userId={user_id}"
WAKEUP_KEYWORD = "小爱同学"

HARDWARE_COMMAND_DICT = {
    # hardware: (tts_command, wakeup_command)
    "LX06": ("5-1", "5-5"),
    "L05B": ("5-3", "5-4"),
    "S12": ("5-1", "5-5"),  # 第一代小爱，型号 MDZ-25-DA
    "S12A": ("5-1", "5-5"),
    "LX01": ("5-1", "5-5"),
    "L06A": ("5-1", "5-5"),
    "LX04": ("5-1", "5-4"),
    "L05C": ("5-3", "5-4"),
    "L17A": ("7-3", "7-4"),
    "X08E": ("7-3", "7-4"),
    "LX05A": ("5-1", "5-5"),  # 小爱红外版
    "LX5A": ("5-1", "5-5"),  # 小爱红外版
    "L07A": ("5-1", "5-5"),  # Redmi 小爱音箱 Play(l7a)
    "L15A": ("7-3", "7-4"),
    "X6A": ("7-3", "7-4"),  # 小米智能家庭屏 6
    "X10A": ("7-3", "7-4"),  # 小米智能家庭屏 10
    # add more here
}

DEFAULT_COMMAND = ("5-1", "5-5")

CHANGE_PROMPT_KEY_WORD = ("更改提示词",)
PROMPT = "你是家里的智能音箱助手，请用简洁的口语化中文回答，300 字以内，只回答文字不要带链接"
# 只要包含就交给小爱。灯、空调、电视这类设备名不放这里，否则「电视剧」也会命中；
# 设备控制由 intent.py 按「设备 + 开关/调节动作」判断
XIAOAI_KEYWORDS = (
    "开灯",
    "关灯",
    "打开",
    "关闭",
    "温度调",
    "调高",
    "调低",
    "调到",
    "音量",
    "大声",
    "小声",
    "闹钟",
    "定时",
    "提醒",
    "几点",
    "天气",
)


@dataclass
class Config:
    hardware: str = "LX06"
    account: str = os.getenv("MI_USER", "")
    password: str = field(default=os.getenv("MI_PASS", ""), repr=False)
    cookie: str = field(default="", repr=False)
    mi_did: str = os.getenv("MI_DID", "")
    mi_device_id: str = os.getenv("MI_DEVICE_ID", "")
    mi_token_path: str = os.getenv("MI_TOKEN_PATH", "~/.mi.token")
    qwen_key: str = field(default=os.getenv("DASHSCOPE_API_KEY", ""), repr=False)
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_options: dict[str, Any] = field(default_factory=dict)
    prompt: str = PROMPT
    change_prompt_keyword: Iterable[str] = CHANGE_PROMPT_KEY_WORD
    start_conversation: str = "开始持续对话"
    end_conversation: str = "结束持续对话"
    xiaoai_keywords: Iterable[str] = XIAOAI_KEYWORDS
    music_dir: str = "./music"
    music_http_host: str = ""
    music_http_port: int = 9528
    favorites_path: str = "favorites.json"
    mute_xiaoai: bool = True
    takeover_filler: str = "思考中"
    mute_window: float = 1.5
    poll_interval: float = 0.5
    idle_after: float = 180
    idle_poll_interval: float = 1.5
    night_start: int = 0
    night_end: int = 7
    night_poll_interval: float = 2.0
    use_command: bool = False
    stream: bool = True
    verbose: int = 0
    tts: Literal[
        "mi", "edge", "azure", "openai", "baidu", "google", "volc", "minimax", "fish"
    ] = "mi"
    tts_options: dict[str, Any] = field(default_factory=dict, repr=False)
    web_port: int = 9530
    web_password: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        self.qwen_base_url = self.qwen_base_url.rstrip("/")
        self.mi_token_path = os.path.expanduser(self.mi_token_path)
        self.music_http_port = int(self.music_http_port)
        self.web_port = int(self.web_port)
        self.poll_interval = float(self.poll_interval)
        self.mute_window = float(self.mute_window)
        self.night_start = int(self.night_start) % 24
        self.night_end = int(self.night_end) % 24
        self.night_poll_interval = float(self.night_poll_interval)
        self.idle_after = float(self.idle_after)
        self.idle_poll_interval = float(self.idle_poll_interval)
        self.verbose = int(self.verbose)

    def missing(self) -> list[str]:
        """启动前还缺的配置，空列表表示可以启动。"""
        missing = []
        if not self.cookie and not (self.account and self.password):
            missing.append("小米账号和密码（account / password）")
        if not self.qwen_key:
            missing.append("千问 API key（qwen_key 或环境变量 DASHSCOPE_API_KEY）")
        return missing

    @property
    def tts_command(self) -> str:
        return HARDWARE_COMMAND_DICT.get(self.hardware, DEFAULT_COMMAND)[0]

    @property
    def wakeup_command(self) -> str:
        return HARDWARE_COMMAND_DICT.get(self.hardware, DEFAULT_COMMAND)[1]

    @classmethod
    def from_options(cls, options: argparse.Namespace) -> Config:
        config = {}
        if options.config:
            config = cls.read_from_file(options.config)
            # 配置文件里的相对路径按配置文件所在目录算，不受启动目录影响
            base = Path(options.config).resolve().parent
            for key in ("music_dir", "favorites_path"):
                path = Path(config.get(key, getattr(cls, key))).expanduser()
                if not path.is_absolute():
                    config[key] = str((base / path).resolve())
        for key, value in vars(options).items():
            if value is not None and key in cls.__dataclass_fields__:
                config[key] = value
        return cls(**config)

    @classmethod
    def read_from_file(cls, config_path: str) -> dict:
        result = {}
        with open(config_path, "rb") as f:
            if config_path.endswith(".json"):
                config = json.load(f)
            else:
                config = yaml.safe_load(f) or {}
        for key, value in config.items():
            if value is None or key not in cls.__dataclass_fields__:
                continue
            if key in ("change_prompt_keyword", "xiaoai_keywords"):
                if not isinstance(value, list):
                    value = [value]
                value = [kw for kw in value if kw]
            result[key] = value
        return result
