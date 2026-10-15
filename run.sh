#!/bin/sh
# 在 NAS 上、项目目录里执行：./run.sh
# 会构建镜像并启动容器。改过代码后重新执行即可，旧容器会被换掉。

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

docker build -t xiaogpt-plus "$ROOT"

if docker inspect xiaogpt >/dev/null 2>&1; then
  docker rm -f xiaogpt >/dev/null
fi

# data：配置、小米登录凭证、收藏列表
# music：只读挂载 NAS 上的音乐目录
docker run -d \
  --name xiaogpt \
  --restart unless-stopped \
  --network host \
  -v "$ROOT/data:/app/data" \
  -v "$MUSIC_DIR:/music:ro" \
  xiaogpt-plus

echo "已启动。"
echo "管理页面：http://<NAS局域网IP>:9530"
echo "看日志：docker logs -f xiaogpt"
