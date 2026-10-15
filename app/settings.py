from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import Config


@dataclass(frozen=True)
class Field:
    key: str  # 支持 qwen_options.model 这种一层嵌套
    label: str
    group: str
    kind: str = "text"  # text / secret / bool / int / float / textarea / lines
    restart: bool = False  # 改了需要重新连接小米
    help: str = ""
    minimum: float | None = None  # int / float 的取值范围
    maximum: float | None = None


FIELDS = (
    Field("account", "小米账号", "小米", restart=True),
    Field("password", "小米密码", "小米", "secret", restart=True),
    Field("hardware", "音箱型号", "小米", restart=True, help="登录后在上面选音箱会自动填"),
    Field("mi_did", "音箱 miotDID", "小米", restart=True),
    Field("mi_device_id", "登录设备号", "小米", restart=True, help="小米按它认设备，换机器部署时保持不变"),
    Field("qwen_key", "API key", "千问", "secret"),
    Field("qwen_base_url", "接口地址", "千问"),
    Field("qwen_options.model", "模型", "千问"),
    Field("qwen_options.enable_thinking", "思考模式", "千问", "bool", help="打开后会先输出推理过程，音箱场景建议关闭"),
    Field("stream", "流式回答", "千问", "bool"),
    Field("prompt", "提示词", "千问", "textarea"),
    Field("mute_xiaoai", "接管时打断小爱", "对话", "bool"),
    Field("takeover_filler", "打断后的垫话", "对话", help="拿到问题后立刻说的一句，顶掉小爱抢说的半句；越短越好，留空不说"),
    Field("mute_window", "压制小爱时长（秒）", "对话", "float", help="拿到问题后持续检查小爱是否在说话的时长", minimum=0, maximum=10),
    Field("xiaoai_keywords", "交给小爱的关键词", "对话", "lines", help="一行一个，句子包含任一词就交给小爱处理"),
    Field("start_conversation", "开始持续对话口令", "对话"),
    Field("end_conversation", "结束持续对话口令", "对话"),
    Field("poll_interval", "查询间隔（秒）", "轮询", "float", help="有人说话时多久查一次对话记录，越小打断越快、请求越多", minimum=0.2, maximum=10),
    Field("idle_after", "多久没人说话算闲置（秒）", "轮询", "float", minimum=10, maximum=86400),
    Field("idle_poll_interval", "闲置时查询间隔（秒）", "轮询", "float", help="闲置后说的第一句会晚一点被发现", minimum=0.2, maximum=60),
    Field("night_start", "夜间开始（点）", "轮询", "int", help="开始和结束相同表示不启用夜间低频；可跨午夜，如 23 到 7", minimum=0, maximum=23),
    Field("night_end", "夜间结束（点）", "轮询", "int", minimum=0, maximum=23),
    Field("night_poll_interval", "夜间查询间隔（秒）", "轮询", "float", minimum=0.2, maximum=60),
    Field("music_dir", "音乐目录", "音乐", restart=True),
    Field("music_http_host", "本机 IP", "音乐", restart=True, help="音箱能访问到的地址，留空自动探测"),
    Field("music_http_port", "音乐端口", "音乐", "int", restart=True),
    Field(
        "music_http_token",
        "播放口令",
        "音乐",
        "secret",
        restart=True,
        help="种子，不是地址里的口令。地址里的口令按日期每天换一个。留空则本次运行自动生成种子",
    ),
)
_BY_KEY = {f.key: f for f in FIELDS}


def _get(config: Config, key: str) -> Any:
    if "." in key:
        parent, child = key.split(".", 1)
        return (getattr(config, parent) or {}).get(child)
    return getattr(config, key)


def _set(config: Config, key: str, value: Any) -> None:
    if "." in key:
        parent, child = key.split(".", 1)
        setattr(config, parent, {**(getattr(config, parent) or {}), child: value})
    else:
        setattr(config, key, value)


def describe(config: Config) -> list[dict[str, Any]]:
    result = []
    for f in FIELDS:
        value = _get(config, f.key)
        item = {
            "key": f.key,
            "label": f.label,
            "group": f.group,
            "kind": f.kind,
            "help": f.help,
            "restart": f.restart,
            "minimum": f.minimum,
            "maximum": f.maximum,
        }
        if f.kind == "secret":
            item["value"] = ""
            item["is_set"] = bool(value)
        elif f.kind == "lines":
            item["value"] = "\n".join(value or [])
        elif f.kind == "bool":
            item["value"] = bool(value)
        else:
            item["value"] = "" if value is None else value
        result.append(item)
    return result


def _coerce(f: Field, raw: Any) -> Any:
    if f.kind == "bool":
        return bool(raw)
    if f.kind in ("int", "float"):
        value = int(raw) if f.kind == "int" else float(raw)
        if f.minimum is not None and value < f.minimum:
            raise ValueError(f"{f.label} 不能小于 {f.minimum:g}")
        if f.maximum is not None and value > f.maximum:
            raise ValueError(f"{f.label} 不能大于 {f.maximum:g}")
        return value
    if f.kind == "lines":
        lines = raw.splitlines() if isinstance(raw, str) else list(raw)
        return [line.strip() for line in lines if str(line).strip()]
    if f.kind == "textarea":
        return str(raw).strip()
    return str(raw).strip()


def apply(config: Config, config_path: str, data: dict[str, Any]) -> list[Field]:
    """校验并写入配置对象和配置文件，返回实际改动的字段。"""
    updates: dict[str, Any] = {}
    for key, raw in data.items():
        f = _BY_KEY.get(key)
        if f is None:
            raise ValueError(f"不支持修改：{key}")
        if f.kind == "secret" and not raw:
            continue  # 留空表示不改
        try:
            value = _coerce(f, raw)
        except (TypeError, ValueError) as e:
            message = str(e) if str(e).startswith(f.label) else f"{f.label} 格式不对"
            raise ValueError(message) from None
        current = _get(config, key)
        if f.kind == "lines":
            current = list(current or [])
        if value != current:
            updates[key] = value

    for key, value in updates.items():
        _set(config, key, value)
    config.qwen_base_url = config.qwen_base_url.rstrip("/")
    if updates:
        write_yaml(config_path, updates)
    return [_BY_KEY[key] for key in updates]


# ---- 只改动相关行地写回 yaml，保留用户的注释 ----

_SCALAR_LINE = re.compile(
    r"""^(?P<head>\s*[\w.-]+:)(?P<value>\s*(?:"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^#\n]*?))(?P<comment>\s+#.*)?$"""
)


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _block_lines(key: str, value: Any, indent: str = "") -> list[str]:
    if isinstance(value, list):
        return [f"{indent}{key}:"] + [f"{indent}  - {_scalar(v)}" for v in value]
    return [f"{indent}{key}: {_scalar(value)}"]


def _find_top(lines: list[str], key: str) -> int:
    pattern = re.compile(rf"^{re.escape(key)}:(\s|$)")
    return next((i for i, line in enumerate(lines) if pattern.match(line)), -1)


def _block_end(lines: list[str], start: int) -> int:
    end = start + 1
    while end < len(lines) and lines[end] and (lines[end][0].isspace() or lines[end].startswith("-")):
        end += 1
    return end


def _replace_scalar(line: str, value: Any) -> str:
    match = _SCALAR_LINE.match(line)
    comment = (match.group("comment") or "") if match else ""
    head = match.group("head") if match else line.split(":", 1)[0] + ":"
    return f"{head} {_scalar(value)}{comment}"


def write_yaml(path: str, updates: dict[str, Any]) -> None:
    file = Path(path)
    lines = file.read_text(encoding="utf-8").splitlines() if file.exists() else []

    for key, value in updates.items():
        if "." in key:
            parent, child = key.split(".", 1)
            top = _find_top(lines, parent)
            if top < 0:
                lines += [f"{parent}:"] + _block_lines(child, value, "  ")
                continue
            end = _block_end(lines, top)
            pattern = re.compile(rf"^\s+{re.escape(child)}:(\s|$)")
            hit = next((i for i in range(top + 1, end) if pattern.match(lines[i])), -1)
            if hit >= 0:
                lines[hit] = _replace_scalar(lines[hit], value)
            else:
                lines[end:end] = _block_lines(child, value, "  ")
            continue

        top = _find_top(lines, key)
        if top < 0:
            lines += _block_lines(key, value)
        elif isinstance(value, list):
            lines[top : _block_end(lines, top)] = _block_lines(key, value)
        else:
            lines[top] = _replace_scalar(lines[top], value)

    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
