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
├─ Dockerfile                 部署镜像（Python 3.12 + Node 22 + 中文字体）
├─ requirements.txt
├─ .env.example
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
