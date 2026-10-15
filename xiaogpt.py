#!/usr/bin/env python3
import argparse
import asyncio
from pathlib import Path

from config import Config
from mi_gpt import MiGPT
from utils import get_hostname

DEFAULT_CONFIG = Path(__file__).resolve().parent / "xiao_config.yaml"


def main():
    parser = argparse.ArgumentParser(description="小爱音箱：千问对话 + 本地音乐")
    parser.add_argument(
        "--config",
        dest="config",
        help=f"配置文件路径（yaml 或 json），不填时使用 {DEFAULT_CONFIG.name}（存在的话）",
    )
    parser.add_argument("--hardware", dest="hardware", help="小爱 hardware 型号")
    parser.add_argument("--account", dest="account", help="小米账号")
    parser.add_argument("--password", dest="password", help="小米密码")
    parser.add_argument("--cookie", dest="cookie", help="小米 cookie")
    parser.add_argument("--mi_did", dest="mi_did", help="设备 miotDID")
    parser.add_argument("--qwen_key", dest="qwen_key", help="千问 API key")
    parser.add_argument(
        "--qwen_base_url",
        dest="qwen_base_url",
        help="千问 OpenAI 兼容接口地址",
    )
    parser.add_argument("--music_dir", dest="music_dir", help="本地音乐目录")
    parser.add_argument(
        "--music_http_host", dest="music_http_host", help="音箱能访问到的本机 IP"
    )
    parser.add_argument(
        "--music_http_port", dest="music_http_port", type=int, help="音乐 HTTP 端口"
    )
    parser.add_argument(
        "--stream",
        dest="stream",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="千问流式回答",
    )
    parser.add_argument(
        "--mute_xiaoai",
        dest="mute_xiaoai",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="接管时打断小爱自己的回答",
    )
    parser.add_argument(
        "--use_command",
        dest="use_command",
        action="store_true",
        default=None,
        help="用 MIoT command 播报（部分型号需要）",
    )
    parser.add_argument(
        "--tts",
        help="TTS 引擎",
        choices=["mi", "edge", "openai", "azure", "google", "baidu", "volc", "minimax", "fish"],
    )
    parser.add_argument(
        "--verbose", "-v", dest="verbose", action="count", default=None, help="详细日志"
    )

    parser.add_argument("--web_port", dest="web_port", type=int, help="管理页面端口")

    options = parser.parse_args()
    if options.config is None and DEFAULT_CONFIG.exists():
        options.config = str(DEFAULT_CONFIG)
    if options.config:
        if not Path(options.config).is_file():
            raise SystemExit(
                f"找不到配置文件：{options.config}，可以复制 xiao_config.yaml.example 改一份"
            )
        print(f"配置文件：{options.config}")
    config = Config.from_options(options)
    if config.web_password and options.config:
        asyncio.run(run_with_web(config, options.config))
    else:
        if not config.web_password:
            print("未设置 web_password，管理页面不启动")
        if missing := config.missing():
            raise SystemExit("缺少配置：" + "、".join(missing))
        asyncio.run(run(config))


async def run(config: Config) -> None:
    miboy = MiGPT(config)
    try:
        await miboy.run_forever()
    finally:
        await miboy.close()


async def run_with_web(config: Config, config_path: str) -> None:
    """管理页面常驻；主程序配置不全或登录失败时不退出，在页面上修好后重新连接。"""
    from app import Runner, start_web

    runner = Runner(config)
    site = await start_web(runner, config_path, config.web_port)
    try:
        host = get_hostname()
    except OSError:
        host = "127.0.0.1"
    print(f"管理页面：http://{host}:{config.web_port}")
    await runner.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runner.stop()
        await site.cleanup()


if __name__ == "__main__":
    main()
