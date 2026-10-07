# 资产中心服务镜像。构建上下文是本仓库根目录：docker build -t fde-asset-server:<版本> .
FROM python:3.12-slim

# 资产原件存在本地 git 裸仓库里，运行时要调 git
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖单独一层：只改源码时不用重装
COPY pyproject.toml README.md ./
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
print("\n".join(tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]))
PY
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY src ./src
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker/entrypoint.sh /usr/local/bin/fde-asset-entrypoint
RUN chmod 0755 /usr/local/bin/fde-asset-entrypoint

RUN addgroup --gid 1000 fde \
    && adduser --uid 1000 --gid 1000 --home /home/fde --shell /usr/sbin/nologin \
        --disabled-password --gecos "" fde \
    && mkdir -p /data \
    && chown -R fde:fde /data /app

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    FDE_ASSET_DATA_DIR=/data \
    # 容器里的模型配置走环境变量，不读 .env.local
    FDE_ASSET_SKIP_ENV_FILE=1
USER fde
VOLUME /data
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health/live', timeout=2)"
ENTRYPOINT ["fde-asset-entrypoint"]
