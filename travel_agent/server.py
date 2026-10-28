"""FastAPI 服务：多会话 + 阶段日志流式推送。

    python -m travel_agent.server          # 打开 http://127.0.0.1:8000

接口一览
    POST   /api/sessions                  新建会话
    GET    /api/sessions                  会话列表
    GET    /api/sessions/{sid}            会话详情（刷新页面后恢复界面）
    DELETE /api/sessions/{sid}            删除会话
    POST   /api/sessions/{sid}/chat       发一句话，返回 NDJSON 事件流
    POST   /api/sessions/{sid}/resume     提交 HITL 决策，继续执行
    GET    /api/download?name=xxx.md      下载策划书

为什么流式用 NDJSON 而不是 SSE：
    浏览器原生 EventSource 只能发 GET，而 HITL 恢复必须 POST 一段决策 JSON。
    用 fetch + 逐行 JSON，两个接口能共用同一套解析代码。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from . import auth, config
from .agent import build_ceo_agent
from .mcp_gateway import open_toolbox
from .store import DEFAULT_TITLE, new_session
from .streaming import dumps, final_proposal, final_state_message, stream_events

WEB_DIR = Path(__file__).resolve().parent / "web"


class ChatRequest(BaseModel):
    message: str


class ResumeRequest(BaseModel):
    decisions: list[dict]


# --------------------------------------------------------------------------- #
# 生命周期
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .store import make_store

    print("启动中：连接 MCP server ...", flush=True)
    # MCP 工具内部持有客户端连接，连接一断就调不动，所以整个服务期间只连一次
    async with open_toolbox() as box:
        app.state.box = box
        app.state.agent = build_ceo_agent(box)
        app.state.store = make_store()
        app.state.locks = {}
        print("就绪。", flush=True)
        yield


app = FastAPI(title="智能旅游规划 Agent", lifespan=lifespan)

# 访问口令：走应用自带的 Basic Auth，不依赖反代（服务器的 80/443 常被别的项目占着）
if config.ACCESS_PASSWORD:
    app.add_middleware(
        auth.BasicAuthMiddleware,
        username=config.ACCESS_USER,
        password=config.ACCESS_PASSWORD,
    )
    print(f"已启用访问口令：用户名 {config.ACCESS_USER}", flush=True)
else:
    print(
        "⚠ 未设置 ACCESS_PASSWORD：接口没有任何鉴权。"
        "仅限本机使用；公网部署请在 .env 里设好再启动。",
        flush=True,
    )


def _lock_for(session_id: str) -> asyncio.Lock:
    """一个会话同时只跑一轮，避免用户连点导致两条流互相踩。"""
    locks: dict[str, asyncio.Lock] = app.state.locks
    if session_id not in locks:
        locks[session_id] = asyncio.Lock()
    return locks[session_id]


# --------------------------------------------------------------------------- #
# 页面与静态
# --------------------------------------------------------------------------- #
@app.get("/")
async def index() -> FileResponse:
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(500, f"找不到前端页面：{page}")
    return FileResponse(page, media_type="text/html; charset=utf-8")


@app.get("/api/health")
async def health():
    from .pdf import available_engines

    box = getattr(app.state, "box", None)
    store = getattr(app.state, "store", None)
    return {
        "ok": box is not None,
        "tools": len(box.all_tools) if box else 0,
        "failed_servers": box.failed if box else {},
        "store": type(store).__name__ if store else None,
        "pdf_engines": available_engines(),
    }


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
@app.post("/api/sessions")
async def create_session():
    session = new_session()
    await app.state.store.save(session)
    return {"id": session["id"], "title": session["title"], "status": session["status"]}


@app.get("/api/sessions")
async def list_sessions():
    return {"sessions": await app.state.store.list_summaries()}


@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    session = await app.state.store.get(sid)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    ok = await app.state.store.delete(sid)
    if not ok:
        raise HTTPException(404, "会话不存在")
    return {"deleted": True}


class RenameRequest(BaseModel):
    title: str


@app.patch("/api/sessions/{sid}")
async def rename_session(sid: str, req: RenameRequest):
    """改会话名。前端双击标题就能改。"""
    session = await app.state.store.get(sid)
    if not session:
        raise HTTPException(404, "会话不存在")

    title = req.title.strip()[:60]
    if not title:
        raise HTTPException(400, "名字不能为空")

    session["title"] = title
    await app.state.store.save(session)
    return {"id": sid, "title": title}


# --------------------------------------------------------------------------- #
# 核心：跑一轮并流式推事件
# --------------------------------------------------------------------------- #
async def _run_turn(session: dict, payload, cfg: dict):
    """跑一轮，把过程同步进会话日志，并把事件流推给前端。"""
    agent = app.state.agent
    steps: dict[str, dict] = {}  # step id → step（dict 保序，所以顺序就是发生顺序）
    status = "done"

    async with _lock_for(session["id"]):
        session["status"] = "running"
        session["pending"] = None
        await app.state.store.save(session)

        async for event in stream_events(agent, payload, cfg):
            kind = event["type"]

            if kind == "step":
                step_id = event["id"]
                if step_id in steps:
                    steps[step_id].update(
                        {k: v for k, v in event.items() if k not in {"type", "id"}}
                    )
                else:
                    steps[step_id] = {k: v for k, v in event.items() if k != "type"}
                yield dumps(event)

            elif kind == "interrupt":
                session["log"] = list(steps.values())
                session["pending"] = event.get("items") or []
                session["status"] = "waiting"
                await app.state.store.save(session)
                yield dumps(event)
                return

            elif kind == "error":
                status = "error"
                session["log"] = list(steps.values())
                session["status"] = "error"
                await app.state.store.save(session)
                yield dumps(event)
                return

            else:
                yield dumps(event)

        # 正常结束：只有 CEO 真的调用了 publish_proposal 才算产出策划书
        message = await final_state_message(agent, cfg)
        file = await final_proposal(agent, cfg)

        proposal = ""
        if file:
            saved = config.OUTPUT_DIR / file
            if saved.is_file():
                proposal = saved.read_text(encoding="utf-8")

        # 有策划书就展示策划书正文（而不是"已保存"那句寒暄）
        display = proposal or message
        session["log"] = list(steps.values())
        session["result"] = {"message": display, "file": file}
        session["status"] = status
        await app.state.store.save(session)
        yield dumps({"type": "result", "message": display, "file": file})


def _cfg(sid: str) -> dict:
    return {"configurable": {"thread_id": sid}}


@app.post("/api/sessions/{sid}/chat")
async def chat(sid: str, req: ChatRequest):
    session = await app.state.store.get(sid)
    if not session:
        raise HTTPException(404, "会话不存在")
    if session.get("status") == "waiting":
        raise HTTPException(409, "这个会话还有待确认的动作，先处理它")

    message = req.message.strip()
    if not message:
        raise HTTPException(400, "消息不能为空")

    if session.get("title") in (None, "", DEFAULT_TITLE):
        session["title"] = message[:24]
        await app.state.store.save(session)

    payload = {"messages": [{"role": "user", "content": message}]}
    return StreamingResponse(
        _run_turn(session, payload, _cfg(sid)), media_type="application/x-ndjson"
    )


@app.post("/api/sessions/{sid}/resume")
async def resume(sid: str, req: ResumeRequest):
    session = await app.state.store.get(sid)
    if not session:
        raise HTTPException(404, "会话不存在")

    pending = session.get("pending") or []
    if not pending:
        raise HTTPException(409, "这个会话没有待确认的动作（可能已经处理过，或服务重启过）")

    counts = [
        (item.get("interrupt_id"), len(item.get("actions") or [])) for item in pending
    ]
    total = sum(count for _iid, count in counts)
    if len(req.decisions) != total:
        raise HTTPException(400, f"决策数量不对：需要 {total} 条，收到 {len(req.decisions)} 条")

    if len(counts) == 1:
        resume_value: dict = {"decisions": req.decisions}
    else:
        # 多个中断同时挂起时，必须按 interrupt_id 分别映射
        resume_value = {}
        offset = 0
        for interrupt_id, count in counts:
            resume_value[interrupt_id] = {"decisions": req.decisions[offset : offset + count]}
            offset += count

    return StreamingResponse(
        _run_turn(session, Command(resume=resume_value), _cfg(sid)),
        media_type="application/x-ndjson",
    )


# --------------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------------- #
@app.get("/api/download")
async def download(name: str):
    root = config.OUTPUT_DIR.resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=name, media_type="text/markdown; charset=utf-8")


@app.get("/api/proposal.pdf")
async def proposal_pdf(name: str):
    """把已落盘的策划书转成 PDF 下载。

    第一次请求时现场转换，之后复用（PDF 比 md 新就不重转）。
    转换是 CPU/进程密集的（尤其 Word 引擎），所以扔到线程池里跑，别堵住事件循环。
    """
    from .pdf import render as render_pdf

    root = config.OUTPUT_DIR.resolve()
    md_path = (root / name).resolve()
    if root not in md_path.parents or not md_path.is_file():
        raise HTTPException(404, "策划书不存在")

    pdf_path = md_path.with_suffix(".pdf")
    if pdf_path.is_file() and pdf_path.stat().st_mtime >= md_path.stat().st_mtime:
        return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)

    result = await asyncio.to_thread(
        render_pdf, md_path.read_text(encoding="utf-8"), pdf_path
    )
    if not result.get("ok"):
        detail = result.get("error", "转换失败")
        reasons = "；".join(result.get("errors") or [])
        raise HTTPException(
            501,
            f"{detail}{('：' + reasons) if reasons else ''}。{result.get('hint', '')}".strip(),
        )

    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)


# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="智能旅游规划 Agent（Web）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skip", action="append", default=None, help="跳过某个 MCP server")
    args = parser.parse_args()

    if args.skip:
        config.MCP_SKIP = list(config.MCP_SKIP) + args.skip

    print(f"打开浏览器访问：http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
