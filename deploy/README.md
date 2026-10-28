# 部署到云服务器（不用域名，直接 IP 访问）

设计目标：**不碰你服务器上已有的博客和其他项目**。

- 不装反代、不占 80/443
- 应用直接监听 `0.0.0.0:8080`，浏览器访问 `http://<公网IP>:8080`
- 鉴权由**应用自带的 Basic Auth** 负责（`.env` 里的 `ACCESS_USER` / `ACCESS_PASSWORD`）

```
朋友浏览器 ──▶ http://49.232.174.53:8080  ──▶ systemd: travel_agent.server
                                              （自带账号密码，单进程）
```

## 上线前要解决的 4 件事，落在哪

| # | 问题 | 解决方式 |
|---|---|---|
| ① | 应用本身**没有鉴权** | `travel_agent/auth.py` 里的 ASGI 中间件，读 `.env` 的 `ACCESS_PASSWORD`；不配也能跑，但启动会警告 |
| ② | **多 worker 会让 HITL 失效**（中断状态在进程内存里） | `travel-agent.service` 的 `ExecStart` 固定单进程 |
| ③ | 流式输出被中间层缓冲 | 没有中间层了，直连应用，天然不缓冲 |
| ④ | 生成的策划书要留下来 | 原生部署天然存在 `/opt/travel-agent/output` |

## 前置检查（3 条命令）

```bash
# 1) 端口有没有被别的项目占着（改端口用 APP_PORT=8090）
ss -lntp | grep ':8080'

# 2) 系统版本
cat /etc/os-release | head -2      # 需要 Ubuntu 22.04 / 24.04

# 3) 云控制台：腾讯云 → 安全组 / 防火墙 → 入站规则
#    放通 TCP 8080（只在服务器里开 ufw 不够，这是最容易漏的一步）
```

## 部署步骤

### 1. 拉代码

```bash
sudo mkdir -p /opt/travel-agent
sudo git clone https://github.com/zn-cu/TravelAgent.git /opt/travel-agent
cd /opt/travel-agent
```

### 2. 填配置

```bash
sudo cp .env.example .env
sudo nano .env
```

必填三项：

```dotenv
DEEPSEEK_API_KEY=sk-...            # 模型 key
AMAP_MAPS_API_KEY=...              # 高德 key
ACCESS_USER=sunlianwu              # 访问账号
ACCESS_PASSWORD=换成随机串          # 访问密码（不填 = 无鉴权，启动会警告）
```

> 密码别用 `sunlianwu` 这种和账号一样的串。生成一个：
> `openssl rand -base64 12`
> 几个朋友共用这一个账号密码，改密码就是改 `.env` 再 `systemctl restart travel-agent`。

### 3. 一键装环境并启动

```bash
sudo bash deploy/install.sh
```

它会：检查端口 → 装系统包（中文字体 / PDF 依赖）→ 装 Node 22 →
`npm i -g 12306-mcp` → 建 venv 装依赖 → 写 systemd unit 并启动 → `ufw allow 8080`。

想换端口：

```bash
sudo APP_PORT=8090 bash deploy/install.sh
```

### 4. 验证

```bash
systemctl status travel-agent --no-pager          # active (running)
journalctl -u travel-agent -n 40 --no-pager       # 看 MCP 连上没、工具数
curl -i http://127.0.0.1:8080/api/health          # 期望 401（说明鉴权生效）
curl -u sunlianwu:你的密码 http://127.0.0.1:8080/api/health   # 期望 200
```

浏览器打开 `http://49.232.174.53:8080` → 弹登录框 → 输入账号密码。

## 常用运维命令

```bash
sudo systemctl restart travel-agent     # 改完 .env 或代码后重启
sudo systemctl stop travel-agent
journalctl -u travel-agent -f           # 实时日志（含 MCP 连接、每次工具调用）
tail -f /opt/travel-agent/logs/trace.jsonl   # 工具调用明细
```

改代码后更新：

```bash
cd /opt/travel-agent && sudo git pull && sudo systemctl restart travel-agent
```

## 常见问题

| 症状 | 原因 | 处理 |
|---|---|---|
| 浏览器连不上、`curl` 超时 | 云安全组没放通 8080 | 控制台 → 安全组 → 入站加 TCP 8080 |
| 本机 8080 上 `curl` 通、外面不通 | 同上（防火墙分两层：ufw + 云安全组） | 同上 |
| 一直弹登录框、输了也不对 | `.env` 里 `ACCESS_PASSWORD` 有空格或引号 | 改完 `systemctl restart travel-agent` |
| 502 / 页面空白 | 应用没起来 | `journalctl -u travel-agent -n 80`，多半是缺 key |
| 日志一直不动 | 浏览器/网络侧缓冲 | 本项目直连、不缓冲；若用了公司代理需放行流式响应 |
| PDF 里中文是方块 | 缺字体 | `fc-list \| grep -i noto` 确认装了 `fonts-noto-cjk` |
| 12306 一直超时 | npx 在下载 / 网络不通 | `command -v 12306-mcp` 要有输出；没有就重跑 `npm i -g 12306-mcp` |
| 内存吃光 | PDF 渲染抖动 | systemd 已设 `MemoryMax=2G`，超了自动重启；可再加 2G swap |
| 重启后会话全没了 | 会话存内存 | 装 Redis 并设 `REDIS_URL`，见下 |

## 可选：加 Redis 存会话

不装的话会话只在内存里，**服务重启就全丢**，多人用也看不到彼此记录。

```bash
sudo apt-get install -y redis-server
sudo systemctl enable --now redis-server
```

`.env` 里加：

```dotenv
REDIS_URL=redis://127.0.0.1:6379/0
```

`sudo systemctl restart travel-agent` 后，日志里出现「会话存储：Redis（...）」即生效。

## 安全提醒（IP + HTTP 的实际风险）

现在**没有 HTTPS**，账号密码和对话内容是明文过公网的。给几个朋友临时用可以，
但要清楚代价：

- 同一个 Wi-Fi、同一个运营商链路，或者云厂商内部，都可能被动看到密码
- 撞开密码 = 别人可以用你的 DeepSeek 额度

降低风险的做法（都不影响博客）：

1. **密码换成 12 位以上随机串**，别用 `sunlianwu`
2. 只把地址给信得过的朋友，别贴到公开群里
3. 用完可以 `sudo systemctl stop travel-agent` 直接关掉
4. 想加密又不想占 80/443：让 Caddy 只听 `:8443` 用自签证书（浏览器提示一次"继续访问"），
   见下面的附录

## 附录：想要 HTTPS / 域名

如果以后想上 HTTPS，加一层 Caddy 即可，但**注意 80/443 已被你的博客占用**，
所以要么用别的端口，要么在你的既有反代里加一段代理规则。

独立端口 + 自签证书（不占 80/443）：

```caddy
# /etc/caddy/Caddyfile
:8443 {
	tls internal
	reverse_proxy 127.0.0.1:8080
}
```

`systemctl reload caddy` 后访问 `https://49.232.174.53:8443`，
首次会有证书警告，点"继续访问"即可。此时账号密码走 TLS 加密。

有域名且**服务器在大陆**时还需要 ICP 备案，详见 `README.md` 的历史版本或另起文档。

## 附录：想用 Docker

`Dockerfile` 可用（Node 22 + 中文字体都装好了），但要多做两件事：
把 `output/` 挂成 volume、且**只能单容器单进程**（同样不能开多 worker）。
