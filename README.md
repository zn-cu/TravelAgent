# 智能旅游规划 Agent

> 说一句「我想去成都玩3天」，四个智能体分工合作，产出一份可执行的《旅游策划书》——
> 景点来自高德、车次票价来自 12306、预算来自真实核算，落盘之前必须你点头。

基于 **deepagents**（LangGraph 多智能体）+ **MCP**（工具协议）+ **Skills**（领域知识）
+ **FastAPI**（流式界面）的学习型项目。

**当前版本：v1.0.0**

---

## 它是什么

| 能力 | 说明 |
|---|---|
| **一句话出方案** | 需求模糊也能接：缺信息会先追问，够了才派活 |
| **真实数据** | 高德 MCP 查景点/路线/天气，12306 MCP 查车次/票价/余票 |
| **四个智能体协作** | 项目经理拆解 → 景点推荐官 / 出行规划师 / 财务管家 → 项目经理汇总 |
| **过程看得见** | Web 界面实时打日志：谁在派活、谁在查什么、花了多久 |
| **人在回路** | 方案必须你确认；超预算会单独拦一次 |
| **多会话** | 新建/切换/重命名/删除，刷新不丢 |
| **导出** | Markdown 下载 + 服务端渲染 PDF（或退化为浏览器打印） |
| **双入口** | Web（`travel_agent.server`）与 CLI（`travel_agent.cli`）共用同一套 Agent |

---

## 界面长什么样

```
执行流程
  ✓ 项目经理        正在思考                  2.1s
  ✓ 项目经理        派活给 attraction-specialist
  ✓ 景点推荐官      正在思考                  6.4s
  ✓ 景点推荐官      搜索景点                  1.2s
  ✓ 景点推荐官      查景点详情          ×6   1.0s
  ✓ 项目经理        派活给 transport-planner
  ✓ 出行规划师      查询车次余票              2.8s
  ✓ 财务管家        核算预算                  0.3s
  ● 项目经理        请求用户确认

请确认这份方案
  目的地：成都 ｜ 天数：3 天 2 晚 ｜ 人数：2 人
  出发地：西安 ｜ 日期：11-04 ～ 11-06
  交通：G3437 西安北 07:33 → 成都东 11:06，二等座 ¥730×2（门到门 5.0h）
  预算：合计 ¥3300 + 机动金 ¥330 = ¥3630，人均 ¥1815（预算 ¥3000 → 超 ¥630）
  [通过] [修改参数] [否决]              ▸ 查看原始参数

                                [下载 Markdown] [导出 PDF]
```

刻意**不做打字机效果**：用户想知道的不是每个字，而是"走到哪一步了"。
正文增量只用来判断某个智能体开始干活，一个字都不往前端推。

---

## 示例产出

不想先跑起来也能看到实际结果：

| 文件 | 说明 |
|---|---|
| [docs/examples/北京2天2人.md](docs/examples/北京2天2人.md) | 天津出发、2 人 2 天，含真实车次、天气、预算与地图链接 |
| [docs/examples/北京2天2人.pdf](docs/examples/北京2天2人.pdf) | 同一份内容的后端渲染 PDF（`weasyprint` 引擎产出） |
| [docs/examples/张掖2天2人.md](docs/examples/张掖2天2人.md) | 西安出发、预算 3000 的"紧凑版"，可看它如何处理"门票查不到" |

这两份是真实跑出来的，留着大模型真实取舍的痕迹 —— 比如门票拿不到时如实写「待补充」
而不是编一个数字。这正是 Skills 与工具设计想达到的效果。

---

## 快速开始

### 0. 环境要求

- **Python ≥ 3.10**
- **Node.js ≥ 22**（12306 MCP 通过 `npx` 启动，Node 18 跑不起来）

### 1. 安装依赖

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

用 uv 的话（本项目没有 pyproject，直接用 requirements 就行）：

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt   # Windows: .venv\Scripts\python.exe
```

### 2. 配置密钥

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

至少填这两项：

```dotenv
DEEPSEEK_API_KEY=sk-...          # 或换 OPENAI_API_KEY / ANTHROPIC_API_KEY
AMAP_MAPS_API_KEY=...            # https://console.amap.com/dev/key 免费申请
```

### 3. 自检（强烈建议）

```bash
python check_env.py
```

它会依次检查：版本 / 依赖 / 配置 / 自研预算工具 / **三个 MCP 握手** / PDF 引擎（可选）。
MCP 那一步会打印出**真实工具名**，是排查"某个专家没工具"的最快入口。

### 4. 运行

```bash
# Web（推荐）
python -m travel_agent.server
# 打开 http://127.0.0.1:8000

# 或者 CLI
python -m travel_agent.cli "我想去成都玩3天"
```

试试这句话：

> 我想去成都玩3天，从西安出发，2 个人，预算 3000

---

## 架构

```
                      用户（Web / CLI）
                             │
                    ┌────────▼─────────┐
                    │  CEO 项目经理      │  deepagents 提供：待办清单 / 文件系统 /
                    │  （LangGraph）     │  task 分派 / 上下文摘要 / Skills / 中断
                    └──┬─────┬─────┬───┘
            task 派活  │     │     │
        ┌──────────────┘     │     └──────────────┐
        ▼                    ▼                    ▼
  景点推荐官            出行规划师            财务管家
  POI + 天气        车次/票价 + 路线          预算核算
        │                    │                    │
        └────────────────────┴────────────────────┘
                             │ MCP
                    ┌────────▼─────────┐
                    │  MCP 网关         │  逐个连接 → tools/list → 能力映射
                    └─┬──────┬──────┬──┘
                      ▼      ▼      ▼
                   高德    12306   自研预算
               （远程HTTP）（stdio）（stdio）
```

**五层各管什么：**

| 层 | 技术 | 解决的问题 |
|---|---|---|
| 工具 | **MCP** | 能力从哪来（高德 / 12306 / 自研预算服务） |
| 知识 | **Skills** | 活该怎么干（4 份 `SKILL.md`，启动只读摘要，用到才读全文） |
| 编排 | **deepagents** | 谁干、怎么分派、怎么汇总（CEO 用自动生成的 `task` 工具） |
| 纪律 | **Middleware** | 横切关注点：调用日志、12306 限流 |
| 刹车 | **HITL** | 什么时候停下来问人：方案确认、超预算 |

---

## 一次请求的完整链路

```
用户：我想去成都玩3天
  └─ CEO 拆解 → 缺「出发地」→ 直接回复里问，本轮结束（不落盘、不派活）
     └─（用户补：西安出发，2 人，预算 3000）
        ├─ task(景点推荐官)
        │    └─ 读 skills/attraction-scout → 高德 maps_text_search / detail / weather
        │       → 按天排景点表（含坐标、门票、避坑）
        ├─ task(出行规划师)
        │    └─ 读 skills/transport-planner → 12306 查往返 ← 限流中间件介入
        │       → 本地估算飞机 → 四项对比（门到门口径）
        ├─ task(财务管家)
        │    └─ 读 skills/budget-keeper → 预算 MCP 核算 → 分项+合计+10% 机动金
        ├─ 按 travel-proposal 模板汇总成《旅游策划书》
        ├─ build_amap_links        → 每景点一条 https 高德链接
        ├─ maps_schema_personal_map→ 专属地图唤端链接（附加）
        ├─ confirm_itinerary       ✋ 中断 #1：通过 / 修改参数 / 否决
        ├─ book_hold               超预算？——是——▶ ✋ 中断 #2
        └─ publish_proposal        → 落盘 output/策划书-*.md（前端出下载按钮）
```

**顺序上的讲究**：景点和交通互不依赖，可以并派；预算必须**等**前两者回来再派，
否则财务管家只能瞎编。这一点写在 CEO 的 prompt 里。

---

## 目录结构

```
旅游规划agent/
├─ check_env.py               环境自检（会打印 MCP 真实工具名）
├─ Dockerfile                 可选：容器化部署（Python 3.12 + Node 22 + 中文字体）
├─ requirements.txt
├─ .env.example
│
├─ deploy/                    原生部署包（推荐）
│   ├─ install.sh             一键装环境（系统包 / Node 22 / 12306-mcp / venv / systemd）
│   ├─ travel-agent.service   systemd 单元（单进程 + 只绑 127.0.0.1）
│   ├─ Caddyfile              反代：HTTPS + 共享账号 + 不缓冲流式
│   └─ README.md              部署步骤与故障排查
│
├─ travel_agent/
│   ├─ config.py              模型 / MCP 配置 / 能力映射 / 阈值
│   ├─ mcp_gateway.py         逐个连 MCP → 能力映射 → 分发给专家
│   ├─ middleware.py          2 个中间件：调用日志、12306 限流
│   ├─ subagents.py           3 个专家定义 + 本地飞机估价工具
│   ├─ agent.py               CEO 组装（HITL 配置、publish/地图工具）
│   ├─ streaming.py           astream → 阶段日志事件（不 import langchain，可单测）
│   ├─ store.py               会话存储：内存 / Redis
│   ├─ publish.py             策划书落盘 + 长度校验
│   ├─ geo.py                 高德 https 链接生成
│   ├─ pdf.py                 Markdown → PDF（三引擎可插拔）
│   ├─ server.py              FastAPI：多会话 + 流式接口 + 下载
│   ├─ cli.py                 命令行入口
│   └─ web/index.html         单文件前端（无需构建）
│
├─ mcp_server/                自研 MCP：只有 calculate_budget
│   ├─ budget_core.py         预算解析与核算（纯标准库）
│   ├─ server.py              FastMCP 暴露成 MCP 工具
│   └─ smoke_test.py          MCP 协议自检（不花 token）
│
├─ skills/                    4 份技能包（纯 markdown，不含代码）
│   ├─ attraction-scout/      怎么用高德搜景点、怎么挑、怎么排进 3 天
│   ├─ transport-planner/     怎么把城市转车站码、怎么比高铁和飞机
│   ├─ budget-keeper/         金额口径、机动金规则、输出格式
│   └─ travel-proposal/       策划书模板与汇总规则
│
├─ tests/                     单元测试（不需要联网）
└─ output/                    生成的策划书（.md / .pdf）
```

## 配置项

全部写在 `.env`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | 必填（或换 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`） |
| `TRAVEL_MODEL` | `deepseek:deepseek-chat` | 模型，格式 `provider:model` |
| `AMAP_MAPS_API_KEY` | — | 高德 key，缺了景点推荐官没工具 |
| `BUDGET_LIMIT` | `5000` | 预算红线，超过触发第二道 HITL |
| `RAIL_MIN_INTERVAL` | `3.0` | 两次 12306 查询最小间隔（秒） |
| `RAIL_MAX_CALLS` | `20` | 单会话最多查 12306 几次 |
| `MCP_TIMEOUT` | `90` | 单个 MCP server 连接超时（npx 首次拉包慢，给足） |
| `MCP_SKIP` | 空 | 临时跳过某些 server，逗号分隔：`amap,rail12306` |
| `REDIS_URL` | 空 | 填了就切 Redis 存会话，否则用内存 |
| `PDF_ENGINE` | `auto` | `auto` / `weasyprint` / `xhtml2pdf` / `word` |

命令行也有等价开关：`--skip amap`、`--timeout 30`、`--list-tools`（只自检 MCP）。

---

## 关键设计（为什么这么写）

| 决定 | 原因 |
|---|---|
| 工具走 **MCP** 而非直接 `@tool` | 工具能脱离 Agent 独立测试、能被别的 MCP 客户端复用；顺带学会了「自研 MCP + 第三方 MCP 混用」 |
| **能力映射**而不是硬编码工具名 | 第三方工具名会变（`get-tickets` / `get_tickets`），按候选名匹配 + 去重，缺了只 warn 不崩 |
| 每个专家**只给一个工具** | 权限最小化：景点推荐官看不到预算，财务管家查不了车票，比在 prompt 里写"别越界"可靠 |
| Skills 只写**怎么做** | 4 份纯 markdown，零代码；描述写具体才能被正确命中 |
| 中间件只做 2 个 | 调用日志（调试全靠它）+ 12306 限流（第三方接口必须防限流）。其余都是过度设计 |
| HITL 只留 2 处 | 方案确认（必经）+ 超预算条件中断（平时不打扰） |
| 落盘靠 `publish_proposal` | 不能靠"最后一条 AI 文本"——追问、寒暄都会被存成"策划书"。工具还拒绝 <120 字的内容 |
| PDF 三引擎可插拔 | `weasyprint`（服务器）/ `xhtml2pdf`（纯 wheel）/ `word`（仅 Windows），强依赖环境所以必须能换 |

---

## 测试

```bash
python -m unittest discover tests -v
```

全部**不需要联网、不需要 API Key**。覆盖：

| 文件 | 覆盖内容 |
|---|---|
| `test_budget.py` | 预算解析（中文标点、`金额*数量`、脏输入） |
| `test_ratelimit.py` | 限流器（间隔、上限、超额返回） |
| `test_store.py` | 会话存储（增删改查、日志截断、排序） |
| `test_streaming.py` | 流式事件（工具调用、专家名反查、中断、异常、落盘判定） |
| `test_publish.py` | 落盘校验（短内容拒绝、同名不覆盖、自动建目录） |
| `test_geo.py` | 高德链接（坐标解析、URL 编码、部分失败） |
| `test_pdf.py` | PDF 引擎选择、失败兜底、异常后继续试下一个 |

---

## 部署

**原生部署（systemd），不用 Docker、不用反代**，4 核 4G 足够：

```
朋友浏览器 ──▶ http://<公网IP>:8080 ──▶ systemd: travel_agent.server（单进程，自带账号密码）
```

不占 80/443，所以和服务器上已有的博客、其他项目**互不干扰**。
完整步骤（端口检查、云安全组、口令配置、Redis、故障排查）见 **[deploy/README.md](deploy/README.md)**。

四件事必须在部署时做到：

| # | 为什么 | 落在哪 |
|---|---|---|
| 设 `ACCESS_PASSWORD` | 应用自带 Basic Auth；不设就是裸奔，谁的能拿你额度烧钱 | `travel_agent/auth.py` + `.env` |
| **单进程**（不加 `--workers`） | HITL 中断状态在进程内存里，多进程会让"确认"随机失效 | `deploy/travel-agent.service` |
| 云安全组放通应用端口 | 只在服务器里开 ufw 不够 | 云控制台 |
| 生成的文件要留得住 | 原生部署天然满足（`/opt/travel-agent/output`） | 无需额外操作 |

想上 HTTPS 时再加一层反代（自签或域名），见 `deploy/README.md` 的附录。

**上线前必做**：把 `AMAP_MAPS_API_KEY` 换成正式申请的新 key；`.env` 不进版本库。

---

## 排错

| 症状 | 先看哪 |
|---|---|
| 启动时某个 MCP 连不上 | `python check_env.py`，或 `--list-tools`；连不上会被跳过并打印原因 |
| 专家没有工具 | 能力映射没命中 → 把 check_env 打印的真实工具名补进 `config.CAPABILITIES` |
| Agent 自己硬答不派活 | `subagents` 的 `description` 不够具体，或 CEO prompt 强调不足 |
| 一调用工具就失败 | `logs/trace.jsonl` 最后一条的 `status` |
| 卡在"输出中" | 服务端日志；多半是某个工具在超时重试 |
| 追问轮也出现了下载按钮 | 不该发生——落盘只认 `publish_proposal`，用 `output/` 里是否真有文件确认 |
| 提交决策报"决策数量不对" | 一次可能挂了多个待确认动作，必须每张卡片都选 |
| PDF 里中文是方块 | 缺中文字体：Linux 装 `fonts-noto-cjk` |
| 网页表单中文输入乱跳 | 已在 keydown 里判断 `isComposing`；若仍异常请告知浏览器与输入法 |
| 会话刷新后丢失 | 默认内存存储，重启即清空；配 `REDIS_URL` 可持久化 |
| 手机上点「下载」没反应、电脑正常 | ① 只给 `filename*=utf-8''` 没有 ASCII 兜底，手机浏览器猜不到名字就放弃；② `text/markdown` 被当成可显示内容；③ 微信/QQ 内置浏览器不支持下载文件 | 服务端补 ASCII `filename="..."` + 改 `application/octet-stream`；前端改用 `<a download>` 触发；必要时用系统浏览器打开 |

---

## 已知限制

- **机票是估算值**：没有免费可用的机票 MCP（高德不管、12306 不管、飞猪要商务授权），
  `estimate_flight` 是按距离的确定性公式，结果里会标注"估算"。
- **门票经常"待补充"**：高德 POI 不一定返回票价。宁可留空也不让模型编——这是刻意的。
- **12306 是第三方非官方接口**，有风控；限流 + 错误退避，仅适合学习演示，别拿去生产。
- **会话持久化不覆盖中断状态**：配了 Redis 也只是存会话日志，LangGraph 的 checkpointer
  仍在进程内存里，所以"重启后接着处理那个待确认的动作"做不到。
- **单进程内存会话**：不配 Redis 时，重启即丢，也不能多 worker。

---

## 想读代码的话

按这个顺序看，每一层都是下一层的基础：

| 顺序 | 文件 | 能学到什么 |
|---|---|---|
| 1 | `mcp_server/budget_core.py` | 工具的本质就是一个纯函数 |
| 2 | `mcp_server/server.py` | MCP 只是把它挂到 stdio 的 JSON-RPC 上 |
| 3 | `travel_agent/config.py` | 三个 server 配置长什么样、能力映射怎么配 |
| 4 | `travel_agent/mcp_gateway.py` | 怎么把 MCP 工具变成 LangChain 工具并分发 |
| 5 | `travel_agent/middleware.py` | 横切逻辑怎么插进去（最值得反复看） |
| 6 | `travel_agent/subagents.py` | 一个"专家"其实就是 prompt + 工具 + skills |
| 7 | `travel_agent/agent.py` | CEO 怎么组装（`create_deep_agent` 的全部参数与 HITL） |
| 8 | `travel_agent/streaming.py` | 怎么把 LangGraph 的流翻译成用户能看懂的东西 |
| 9 | `travel_agent/server.py` + `web/index.html` | 前后端如何配合完成流式与中断恢复 |

`skills/*/SKILL.md` 建议最后看——它们不含代码，但能说明"为什么模型会那样干活"。
