FROM python:3.10-slim

# TZ 用 POSIX 写法，slim 镜像不带时区数据库也能按北京时间算（日志时间、管理页面锁定到第二天 0 点）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=CST-8 \
    MI_TOKEN_PATH=/app/data/.mi.token

WORKDIR /app

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: -i "$PIP_INDEX_URL" -r requirements.txt

COPY . .

EXPOSE 9528 9530
ENTRYPOINT ["python", "xiaogpt.py"]
CMD ["--config", "/app/data/xiao_config.yaml", "--music_dir", "/music"]
