# 部署到云服务器（原生，不用 Docker）

4 核 4G / 40G 磁盘足够跑。方案：**systemd 跑应用 + Caddy 做对外入口**。
应用只监听 `127.0.0.1:8000`，公网只能通过 Caddy 的 80/443 访问，顺便拿到 HTTPS 和账号密码。

```
公网 ──▶ Caddy(:80/:443, HTTPS + 账号密码) ──▶ uvicorn(:8000, 只绑 127.0.0.1) ──▶ Agent
```

## 上线前要解决的 4 件事，分别落在哪

| # | 问题 | 解决方式 | 在哪 |
|---|---|---|---|
| ① | 应用本身**没有任何鉴权** | Caddy `basic_auth` 一个共享账号；应用只绑 `127.0.0.1`，绕不过去 | `deploy/Caddyfile` + `travel-agent.service` |
| ② | **多 worker 会让 HITL 失效**（中断状态在进程内存里） | systemd 的 `ExecStart` 不加 `--workers`，固定单进程 | `travel-agent.service` |
| ③ | 反代默认**缓冲**响应 → 前端日志卡住不动 | Caddy `flush_interval -1` + 超时 300s（Caddy 默认就不缓冲，这里是显式写死） | `deploy/Caddyfile` |
| ④ | 生成的策划书要留下来 | **原生部署天然就在服务器磁盘上**（`/opt/travel-agent/output`），不存在 Docker 容器重建丢文件的问题 | 无需额外操作 |

> ④ 的补充：只有用 Docker 部署时，容器内目录才是"临时的"，才需要挂 volume。

## 步骤

### 1. 上传代码

```bash
sudo mkdir -p /opt/travel-agent
sudo git clone https://github.com/zn-cu/TravelAgent.git /opt/travel-agent
```

### 2. 填密钥

```bash
cd /opt/travel-agent
sudo cp .env.example .env
sudo nano .env          # 填 DEEPSEEK_API_KEY 和 AMAP_MAPS_API_KEY
```

顺便确认这两行（保持默认即可）：

```dotenv
PDF_ENGINE=auto                 # 服务器上是 weasyprint
REDIS_URL=redis://127.0.0.1:6379/0    # 可选，见第 5 步
```

### 3. 一键装环境

```bash
sudo bash deploy/install.sh
```

它会做：装系统包（中文字体 / WeasyPrint 依赖 / Caddy）→ 装 Node 22 →
`npm i -g 12306-mcp`（避免每次启动都下载）→ 建 venv 装依赖 →
装并启动 systemd 服务 → 放开 80/443。

### 4. 配 Caddy（账号密码 + HTTPS）

**先生成口令哈希**：

```bash
caddy hash-password --plaintext 'sunlianwu'
# 输出形如：$2a$14$xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**再配站点地址**：

```bash
sudo tee /etc/caddy/environment >/dev/null <<'EOF'
SITE_ADDRESS=:80
EOF
```

> 有域名就把 `:80` 换成域名，例如 `SITE_ADDRESS=travel.example.com`，
> Caddy 会自动申请证书、自动续期。**没有域名时就只能用 `:80`，此时密码是明文传输**，
> 只适合临时给朋友试用。

**装配置文件**：

```bash
sudo cp /opt/travel-agent/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile      # 把 <HASH> 换成上一步的输出
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy
sudo systemctl reload caddy
```

> 服务是 `caddy`，不是 `caddy.service` 的别名问题；若 `reload` 报错看 `journalctl -u caddy`。

### 5.（可选但推荐）加 Redis 存会话

不加的话会话只在内存里：**服务重启，所有会话和日志都没了**，多人用也看不到彼此的记录。

```bash
sudo apt-get install -y redis-server
sudo systemctl enable --now redis-server
```

`.env` 里确认有 `REDIS_URL=redis://127.0.0.1:6379/0`，然后 `sudo systemctl restart travel-agent`。
启动日志里会打印「会话存储：Redis（...）」，看到就是生效了。

## 验证

```bash
systemctl status travel-agent --no-pager     # 应该是 active (running)
journalctl -u travel-agent -n 50 --no-pager  # 看 MCP 连上没有、工具数对不对
curl -u sunlianwu:sunlianwu http://127.0.0.1/api/health   # 经 Caddy 的鉴权
```

浏览器打开 `http://服务器IP`，弹账号密码框 → 输 `sunlianwu` / `sunlianwu`。

## 常见问题

| 症状 | 原因 | 处理 |
|---|---|---|
| 输密码后 502 | 应用没起来 | `journalctl -u travel-agent -n 80`；常见是 `.env` 缺 key |
| 页面能开，但日志一直不动 | 反代在缓冲 | 确认用的是本目录的 `Caddyfile`（有 `flush_interval -1`） |
| 聊到一半报 504 | 单轮超过反向代理超时 | `Caddyfile` 里已有 300s；跑超长需求时再调大 |
| PDF 里中文是方块 | 缺字体 | `fc-list \| grep -i noto` 确认 `fonts-noto-cjk` 装了 |
| 12306 一直超时 | npx 在下载或者网络不通 | `npm i -g 12306-mcp` 后重启；`command -v 12306-mcp` 要有输出 |
| 内存被吃光 | PDF 渲染抖动 | systemd 里已设 `MemoryMax=2G`，超了会自动重启；建议再加 2G swap |

## 关于密码

`sunlianwu` / `sunlianwu` **用户名和密码一样，属于弱口令**，扫号脚本几秒就能撞开。
既然只是几个朋友用，建议至少在 Caddy 里换成 `sunlianwu` + 一个 12 位以上的随机串：

```bash
caddy hash-password --plaintext "$(openssl rand -base64 12)"
```

改完 `sudo systemctl reload caddy` 即可，不用重启应用。

## 想用 Docker 的话

仓库里的 `Dockerfile` 仍然是可用的（Node 22 + 中文字体都装好了），
只是要多做两件事：把 `output/` 挂成 volume、且**只能单容器单进程**（同样不能开多 worker）。
