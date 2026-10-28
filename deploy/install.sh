#!/usr/bin/env bash
# 一键装好运行环境（Ubuntu 22.04 / 24.04，用 root 执行）
#
#   sudo bash deploy/install.sh
#
# 特点：**不装反代**（不碰 80/443，不影响服务器上已有的博客和其他项目）。
# 应用直接监听 0.0.0.0:8080，鉴权由应用自带的 Basic Auth 负责。
# 可重复执行：已装过的步骤会跳过。
# 建议先自查语法：bash -n deploy/install.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/travel-agent}"
APP_USER="${APP_USER:-travel}"
APP_PORT="${APP_PORT:-8080}"

step() { echo; echo "=== $* ==="; }

step "0/6 端口检查（$APP_PORT 被占用的话换一个：APP_PORT=8090 sudo bash deploy/install.sh）"
if command -v ss >/dev/null 2>&1 && ss -lntp 2>/dev/null | grep -q ":${APP_PORT} "; then
  echo "⚠ 端口 $APP_PORT 已被占用："
  ss -lntp | grep ":${APP_PORT} "
  echo "  换端口重跑：APP_PORT=8090 sudo bash deploy/install.sh"
  exit 1
fi
echo "端口 $APP_PORT 空闲"

step "1/6 系统包：中文字体 + PDF 渲染依赖"
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 shared-mime-info \
  fonts-noto-cjk \
  curl ca-certificates
# libpango* / libgdk-pixbuf* 是 WeasyPrint 的运行时依赖
# fonts-noto-cjk 决定 PDF 里的中文是正常汉字还是方块

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
  echo "缺少 $APP_DIR/.env —— 先复制并填写："
  echo "  cp $APP_DIR/.env.example $APP_DIR/.env && nano $APP_DIR/.env"
  echo "  至少要填 DEEPSEEK_API_KEY、AMAP_MAPS_API_KEY、ACCESS_PASSWORD"
  exit 1
fi
# 用当前端口渲染 unit 文件（避免手改）
sed "s/--port [0-9]*/--port ${APP_PORT}/" \
  "$APP_DIR/deploy/travel-agent.service" > /etc/systemd/system/travel-agent.service
systemctl daemon-reload
systemctl enable --now travel-agent

# 防火墙放通应用端口（云厂商的安全组还要单独放一次，见 deploy/README.md）
if command -v ufw >/dev/null 2>&1; then
  ufw allow "${APP_PORT}/tcp" || true
fi

echo
echo "安装完成。访问地址：http://<服务器公网IP>:${APP_PORT}"
echo "别忘了在云控制台的安全组/防火墙里放通 TCP ${APP_PORT}，否则外面连不上。"
echo
echo "查看状态："
echo "  systemctl status travel-agent --no-pager"
echo "  journalctl -u travel-agent -f"
