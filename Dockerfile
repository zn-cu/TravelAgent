# 部署用镜像：Python 3.12 + Node 22（12306 MCP 需要）+ 中文字体（PDF 需要）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1

# 1) WeasyPrint 的系统库 + 中文字体（没有 fonts-noto-cjk，PDF 里中文会变方块）
# 2) Node 22：12306 MCP 通过 npx 启动，Debian 自带的是 18，不够
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
        shared-mime-info fonts-noto-cjk fonts-noto-cjk-extra \
        curl ca-certificates xz-utils \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node -v && npm -v

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt ./
RUN pip install -r requirements.txt markdown weasyprint

COPY . .

EXPOSE 8000
CMD ["python", "-m", "travel_agent.server", "--host", "0.0.0.0", "--port", "8000"]
