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

有域名（推荐，自动 HTTPS）：

```bash
sudo mkdir -p /etc/caddy
sudo tee /etc/caddy/environment >/dev/null <<'EOF'
SITE_ADDRESS=travel.zncu.xin
EOF
```

只有 IP（临时试用，密码明文传输）：

```bash
sudo tee /etc/caddy/environment >/dev/null <<'EOF'
SITE_ADDRESS=:80
EOF
```

域名模式的前置条件（缺一个 Caddy 就签不出证书）：

1. **DNS**：`travel.zncu.xin` 的 A 记录指向公网 IP（如 `49.232.174.53`），用 `dig +short travel.zncu.xin` 验证
2. **安全组**：云厂商控制台的入站规则要放通 **80 和 443**（只在服务器里开 ufw 不够）
3. **备案**：中国大陆节点上，域名走 80/443 需要 ICP 备案，否则会被云厂商或运营商拦（见文末「关于备案」）

**装配置文件**：

```bash
sudo cp /opt/travel-agent/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile      # 把 <HASH> 换成上一步的输出
sudo mkdir -p /var/log/caddy && sudo chown caddy:caddy /var/log/caddy
sudo systemctl reload caddy
```

> 服务名是 `caddy`；若 `reload` 报错看 `journalctl -u caddy -n 50`。

证书是**自动申请、自动续期**的，不用管。想确认：

```bash
journalctl -u caddy -n 30 | grep -i -E "certificate|acme|tls"
# 或者从外部看
curl -vI https://travel.zncu.xin 2>&1 | grep -i "subject\|issuer\|expire"
```

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

## 关于备案（大陆节点必读）

云服务器在中国大陆时，**域名走 80/443 是要求 ICP 备案的**，未备案会被云厂商或运营商拦截。
典型表现很迷惑人：`ping` 通、`dig` 正常、`ssh` 好用，但 `curl https://你的域名` 就是超时。

先确认你自己的情况：

```bash
# 1. 域名解析对不对（在本地电脑跑）
dig +short travel.zncu.xin          # 或 nslookup travel.zncu.xin

# 2. 80/443 通不通（在本地电脑跑）
curl -sv --max-time 5 http://travel.zncu.xin   # 卡住无响应 → 大概率被拦

# 3. 云控制台看一眼入站规则有没有 80/443（腾讯云：轻量应用服务器 → 防火墙 / CVM → 安全组）
```

三条路的取舍：

| 情况 | 做法 | 代价 |
|---|---|---|
| **已经备案** | 按本文档走 `SITE_ADDRESS=travel.zncu.xin`，全自动 HTTPS | 无 |
| **没备案，只是朋友短期用** | `SITE_ADDRESS=:8443` + `tls internal`（自签证书）。浏览器会提示"不安全"，点"继续访问"即可 | 每次换浏览器都要点一次，且密码仍在 HTTPS 里（比 HTTP 好） |
| **没备案，想长期用** | 要么去办备案（免费，一般 1–3 周，需要在云厂商处提交），要么换成香港/海外节点（无需备案，但国内访问慢） | 时间或延迟 |

> `tls internal` 的写法：把 Caddyfile 里站点块的第一行改成
> `{$SITE_ADDRESS:localhost} {` 保持不变，然后在块内加一行 `tls internal`，
> 并把 `/etc/caddy/environment` 设为 `SITE_ADDRESS=:8443`。
> 自签证书换机器/换域名要重新点一次信任。

**不建议**的做法：为了绕备案把服务挂在 80 端口用 IP 裸跑——一旦被扫描到，账号和额度都不安全。

## 想用 Docker 的话

仓库里的 `Dockerfile` 仍然是可用的（Node 22 + 中文字体都装好了），
只是要多做两件事：把 `output/` 挂成 volume、且**只能单容器单进程**（同样不能开多 worker）。
