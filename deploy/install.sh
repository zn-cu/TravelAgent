#!/usr/bin/env bash
# 一次性装好运行环境（Ubuntu 22.04 / 24.04，用 root 执行）
#
#   sudo bash deploy/install.sh
#
# 假设项目已经放在 /opt/travel-agent（git clone 或 scp 上来都行）。
# 脚本可以重复执行：已装过的步骤会跳过或原地更新。
# 建议先自己过一遍：bash -n deploy/install.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/travel-agent}"
APP_USER="${APP_USER:-travel}"

step() { echo; echo "=== $* ==="; }

step "1/6 系统包：中文字体 + PDF 渲染依赖"
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 shared-mime-info \
  fonts-noto-cjk \
  curl ca-certificates gnupg
# libpango* / libgdk-pixbuf* 是 WeasyPrint 的运行时依赖
# fonts-noto-cjk 决定 PDF 里的中文是正常汉字还是方块

step "1.5/6 Caddy（用官方源，Ubuntu 自带版本偏旧、有的镜像还没开 universe）"
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y --no-install-recommends caddy
fi
caddy version

step "2/6 Node.js 22（12306 MCP 要求 ≥22，apt 仓库默认只给 18）"
NEED_NODE=1
if command -v node >/dev/null 2>&1; then
  MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
  if [ "$MAJOR" -ge 22 ]; then NEED_NODE=0; fi
fi
if [ "$NEED_NODE" -eq 1 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y --no-install-recommends nodejs
fi
node -v
npm -v

step "3/6 预装 12306 MCP（省掉每次启动的 npx 下载）"
# 装了全局命令后，config.py 会优先直接调用它（启动从数秒降到几十毫秒）
if npm install -g 12306-mcp && command -v 12306-mcp >/dev/null 2>&1; then
  echo "已安装：$(command -v 12306-mcp)"
else
  echo "⚠ 全局安装失败，运行时会退回 npx（首次启动慢，且需要联网）"
fi

step "4/6 运行用户 + 目录"
id -u "$APP_USER" >/dev/null 2>&1 || \
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
if [ ! -d "$APP_DIR" ]; then
  echo "找不到 $APP_DIR，请先把项目放上去："
  echo "  git clone <你的仓库> $APP_DIR"
  exit 1
fi
mkdir -p "$APP_DIR/output" "$APP_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

step "5/6 Python 依赖装进 venv（不动系统 Python）"
if [ ! -d "$APP_DIR/.venv" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip
# weasyprint 单独列出来：它是 PDF 引擎，不在 requirements 的默认依赖里
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" weasyprint

step "6/6 安装并启动 systemd 服务"
if [ ! -f "$APP_DIR/.env" ]; then
  echo "缺少 $APP_DIR/.env —— 先复制并填写密钥："
  echo "  cp $APP_DIR/.env.example $APP_DIR/.env && nano $APP_DIR/.env"
  exit 1
fi
install -m 644 "$APP_DIR/deploy/travel-agent.service" /etc/systemd/system/travel-agent.service
systemctl daemon-reload
systemctl enable --now travel-agent

# 防火墙：只放 Caddy 的 80/443。应用自己的 8000 只监听 127.0.0.1，不对外。
if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
fi

echo
echo "安装完成。接下来："
echo "  1) 配 Caddy（反代 + 账号密码）：见 deploy/README.md 第 4 步"
echo "  2) systemctl status travel-agent --no-pager"
echo "  3) journalctl -u travel-agent -f      # 看实时日志"
