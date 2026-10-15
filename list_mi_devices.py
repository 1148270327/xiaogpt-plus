#!/usr/bin/env python3
"""用本项目已依赖的 miservice-fork 列出小米设备，生成 MI_DID / hardware。

不需要再单独 clone 或安装 miservice_fork 仓库。先把本项目依赖装进当前环境：

    pip install -r requirements.txt

然后任选一种方式传账号：

    export MI_USER=xxx
    export MI_PASS=xxx
    python3 list_mi_devices.py

    python3 list_mi_devices.py --account xxx --password xxx
    python3 list_mi_devices.py --config xiao_config.yaml

首次在新机器登录时，小米会要求安全验证：按提示在浏览器打开链接完成验证后重新运行。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import yaml
from aiohttp import ClientSession
from miservice import MiIOService, MiNAService
from rich.console import Console
from rich.table import Table

from mi_login import MiLoginError, login_mi, resolve_device_id

console = Console()


def load_settings(args: argparse.Namespace) -> dict:
    config = {}
    if args.config:
        with open(args.config, "rb") as f:
            config = yaml.safe_load(f) or {}
    settings = {
        "account": args.account or os.getenv("MI_USER") or config.get("account") or "",
        "password": args.password or os.getenv("MI_PASS") or config.get("password") or "",
        "device_id": os.getenv("MI_DEVICE_ID") or config.get("mi_device_id") or "",
        "token_path": os.path.expanduser(config.get("mi_token_path") or os.getenv("MI_TOKEN_PATH", "~/.mi.token")),
    }
    if not settings["account"] or not settings["password"]:
        raise SystemExit(
            "请提供小米账号密码：--account/--password、环境变量 MI_USER/MI_PASS，"
            "或 --config xiao_config.yaml"
        )
    return settings


def print_mina_devices(devices: list[dict] | None) -> None:
    table = Table(title="小爱音箱 / 带屏设备（对应 README 的 micli mina）")
    table.add_column("name")
    table.add_column("hardware")
    table.add_column("miotDID  ← 填 mi_did / MI_DID")
    table.add_column("deviceID")
    if not devices:
        console.print("[yellow]没有查到小爱设备。可换网络后再试。[/yellow]")
        return
    for item in devices:
        table.add_row(
            str(item.get("name", "")),
            str(item.get("hardware", "")),
            str(item.get("miotDID", "")),
            str(item.get("deviceID", "")),
        )
    console.print(table)


def print_iot_devices(devices: list[dict] | None, show_token: bool) -> None:
    table = Table(title="全部米家设备（对应 README 的 micli list）")
    table.add_column("name")
    table.add_column("model")
    table.add_column("did  ← 也可填 mi_did")
    if show_token:
        table.add_column("token")
    if not devices:
        console.print("[yellow]没有查到米家设备。[/yellow]")
        return
    for item in devices:
        row = [
            str(item.get("name", "")),
            str(item.get("model", "")),
            str(item.get("did", "")),
        ]
        if show_token:
            row.append(str(item.get("token", "")))
        table.add_row(*row)
    console.print(table)


async def fetch_devices(settings: dict) -> tuple[list[dict] | None, list[dict] | None, str]:
    device_id = resolve_device_id(settings["device_id"], settings["token_path"])
    async with ClientSession() as session:
        account = await login_mi(
            session,
            settings["account"],
            settings["password"],
            device_id,
            settings["token_path"],
        )
        mina = await MiNAService(account).device_list()
        iot = await MiIOService(account).device_list()
    return mina, iot, device_id


def main() -> None:
    parser = argparse.ArgumentParser(description="列出小米设备 DID / hardware")
    parser.add_argument("--account", help="小米账号，默认读 MI_USER")
    parser.add_argument("--password", help="小米密码，默认读 MI_PASS")
    parser.add_argument("--config", help="从 yaml/json 配置读取 account/password")
    parser.add_argument(
        "--show-token",
        action="store_true",
        help="同时打印米家设备 token（敏感信息）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出原始结果",
    )
    args = parser.parse_args()
    settings = load_settings(args)

    try:
        mina, iot, device_id = asyncio.run(fetch_devices(settings))
    except MiLoginError as e:
        raise SystemExit(str(e))

    if args.json:
        print(json.dumps({"mina": mina, "iot": iot}, ensure_ascii=False, indent=2))
        return

    print_mina_devices(mina)
    print_iot_devices(iot, args.show_token)
    if mina:
        first = mina[0]
        console.print(
            "建议写入 xiao_config.yaml：\n"
            f"  hardware: \"{first.get('hardware')}\"\n"
            f"  mi_did: \"{first.get('miotDID')}\"\n"
            f"  mi_device_id: \"{device_id}\""
        )


if __name__ == "__main__":
    main()
