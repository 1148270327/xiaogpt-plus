#!/bin/sh
# 在 NAS 上、项目目录里执行：./run.sh
# 镜像里只有 Python 依赖。项目目录挂到 /app，改代码后执行 docker restart xiaogpt。
# 改了 requirements.txt 才需要重新构建：./run.sh --build

# NAS 上放歌的目录，改成实际路径
MUSIC_DIR="/volume1/music"

set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "找不到 docker，请先在 NAS 上安装并启动 Docker"
  exit 1
fi

if [ ! -f "$ROOT/data/xiao_config.yaml" ]; then
  echo "缺少 $ROOT/data/xiao_config.yaml"
  exit 1
fi

if [ ! -d "$MUSIC_DIR" ]; then
  echo "音乐目录不存在：$MUSIC_DIR"
  echo "请修改 run.sh 顶部的 MUSIC_DIR"
  exit 1
fi

if [ "${1:-}" = "--build" ] || ! docker image inspect xiaogpt-plus >/dev/null 2>&1; then
  docker build -t xiaogpt-plus "$ROOT"
fi

if docker inspect xiaogpt >/dev/null 2>&1; then
  docker rm -f xiaogpt >/dev/null
fi

# 项目目录挂到 /app，其中的 data/ 就是配置、登录凭证和收藏
# 音乐目录只读挂到 /music
docker run -d \
  --name xiaogpt \
  --restart unless-stopped \
  --network host \
  -v "$ROOT:/app" \
  -v "$MUSIC_DIR:/music:ro" \
  xiaogpt-plus

echo "已启动。"
echo "管理页面：http://<NAS局域网IP>:9530"
echo "看日志：docker logs -f xiaogpt"
