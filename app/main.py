import json
from fastapi import FastAPI, Query, HTTPException

from pydantic import BaseModel
import os
from app.agents import create_agent_graph
from app.runtime import *
from app.utils import *

app = FastAPI()

class Request(BaseModel):
    text: str

_agent_graph = None

def get_agent_graph():
    global _agent_graph
    if _agent_graph is not None:
        return _agent_graph

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY / GEMINI_API_KEY")

    _agent_graph = create_agent_graph()
    return _agent_graph

def _to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
        return "\n".join([t for t in parts if t]).strip()
    return str(content)

def _extract_tools(messages):
    tools = []
    for m in messages:
        md = m.model_dump() if hasattr(m, "model_dump") else m
        if md.get("type") == "tool":
            name = md.get("name")
            content = md.get("content", "")
            # 盡量 parse 成 JSON
            parsed = None
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                except Exception:
                    parsed = {"raw": content}
            else:
                parsed = content
            tools.append({"name": name, "output": parsed})
    return tools


@app.get("/health")
def health():
    return {"ok": True}

@app.get("/debug/runtime")
def debug_runtime():
    return {
        "run_env": get_run_env(),
        "k_service": os.getenv("K_SERVICE"),
        "workspace_root": str(pick_workspace_root()),
        "cwd": os.getcwd(),
        "can_write_tmp": os.access("/tmp", os.W_OK),
    }

@app.post("/generate")
def generate(req: Request, debug: bool = Query(False)):
    try:
        agent_graph = get_agent_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    
    result = agent_graph.invoke({"messages": [{"role": "user", "content": req.text}]})
    messages = result["messages"]

    last = messages[-1]
    content = last.content if hasattr(last, "content") else last["content"]

    tools_used = _extract_tools(messages)

    resp = {
        "output": _to_text(content),
        "tool_used": tools_used,
    }
    if debug:
        resp["messages"] = [m.model_dump() if hasattr(m, "model_dump") else m for m in messages]
    return resp