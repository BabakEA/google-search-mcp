"""
FastAPI Gateway — Browser Automation MCP
=========================================
Runs on port 9041.  Proxies every call to the MCP server on port 9040 and
also exposes convenience REST endpoints so callers don't need to know the
MCP protocol.

All REST endpoints return JSON.

Environment variables
---------------------
MCP_SERVER_URL   URL of the MCP server  (default: http://localhost:9040/mcp)
API_PORT         Port this gateway listens on  (default: 9041)
API_HOST         Bind address             (default: 0.0.0.0)

Usage
-----
# local
uvicorn api_gateway:app --host 0.0.0.0 --port 9041 --reload

# Docker — started automatically by docker-compose together with mcp_server

Swagger UI:  http://localhost:9041/docs
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

# Research pipeline (from search_api)
from search_api import SearchRequest as ResearchRequest
from search_api import SearchResponse as ResearchResponse
from search_api import _run_pipeline as _research_pipeline

# ── Config ────────────────────────────────────────────────────────────────────
MCP_URL:  str = os.environ.get("MCP_SERVER_URL", "http://localhost:9040/mcp")
API_PORT: int = int(os.environ.get("API_PORT",   "9041"))
API_HOST: str = os.environ.get("API_HOST",       "0.0.0.0")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Browser Automation & Research Gateway",
    description=(
        "Two APIs in one:\n\n"
        "**1. Browser Automation** — Controls a real Chrome browser via the MCP server on port 9040.\n"
        "Launch a browser, navigate pages, click elements, type text, run searches, read full page text.\n\n"
        "**2. Research Pipeline** (`/research`) — Internet research in one call:\n"
        "DuckDuckGo search → fetch pages → LLM relevance filter → per-page summaries → "
        "full Markdown report saved to disk.\n\n"
        "---\n"
        "**Quick test** → scroll to `/research` below, click *Try it out*, "
        "and hit *Execute* — the Babak EA example is pre-filled.\n\n"
        "MCP endpoint: `" + MCP_URL + "`"
    ),
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── MCP tool call helper ──────────────────────────────────────────────────────

async def _call_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> Any:
    """
    Send a JSON-RPC 2.0 `tools/call` request to the MCP server and
    return the parsed content (raises HTTPException on failure).
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(MCP_URL, json=payload)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"MCP server returned HTTP {resp.status_code}: {resp.text[:400]}",
        )
    rpc = resp.json()
    if "error" in rpc:
        raise HTTPException(status_code=500, detail=rpc["error"])
    # MCP returns result.content as a list of {type, text} blocks
    content_blocks = rpc.get("result", {}).get("content", [])
    if not content_blocks:
        return {}
    raw = content_blocks[0].get("text", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health() -> Dict[str, str]:
    """Health check for load-balancers / Docker healthcheck."""
    return {"status": "ok", "mcp_url": MCP_URL}


@app.get("/mcp-status", tags=["meta"])
async def mcp_status() -> Any:
    """Check if the MCP browser session is active."""
    return await _call_mcp_tool("browser_status", {})


# ── Browser lifecycle ─────────────────────────────────────────────────────────

class LaunchRequest(BaseModel):
    headless: bool = True

@app.post("/browser/launch", tags=["browser"])
async def browser_launch(body: LaunchRequest = LaunchRequest()) -> Any:
    """Start a new Chrome instance (headless=True required in Docker)."""
    return await _call_mcp_tool("browser_launch", {"headless": body.headless})


@app.post("/browser/close", tags=["browser"])
async def browser_close() -> Any:
    """Close the browser session."""
    return await _call_mcp_tool("browser_close", {})


# ── Navigation ────────────────────────────────────────────────────────────────

class NavigateRequest(BaseModel):
    url: str

@app.post("/browser/navigate", tags=["navigation"])
async def navigate(body: NavigateRequest) -> Any:
    """Navigate the current tab to a URL."""
    return await _call_mcp_tool("browser_navigate", {"url": body.url})


@app.post("/browser/new-tab", tags=["navigation"])
async def open_new_tab(body: NavigateRequest) -> Any:
    """Open a URL in a new tab."""
    return await _call_mcp_tool("browser_open_new_tab", {"url": body.url})


@app.post("/browser/back", tags=["navigation"])
async def go_back() -> Any:
    """Press the browser Back button."""
    return await _call_mcp_tool("browser_go_back", {})


# ── Tabs ──────────────────────────────────────────────────────────────────────

@app.get("/browser/tabs", tags=["tabs"])
async def list_tabs() -> Any:
    """List all open tabs."""
    return await _call_mcp_tool("browser_list_tabs", {})


@app.post("/browser/tabs/switch/{index}", tags=["tabs"])
async def switch_tab(index: int) -> Any:
    """Switch to a tab by 0-based index."""
    return await _call_mcp_tool("browser_switch_tab", {"tab_index": index})


@app.delete("/browser/tabs/current", tags=["tabs"])
async def close_tab() -> Any:
    """Close the current tab."""
    return await _call_mcp_tool("browser_close_tab", {})


# ── Reading ───────────────────────────────────────────────────────────────────

@app.get("/browser/read", tags=["reading"])
async def read_page() -> Any:
    """Read the full text of the current tab (scroll + expand)."""
    return await _call_mcp_tool("browser_read_page", {})


@app.get("/browser/links", tags=["reading"])
async def get_links(max_links: int = Query(30, ge=1, le=200)) -> Any:
    """Get all hyperlinks from the current page."""
    return await _call_mcp_tool("browser_get_page_links", {"max_links": max_links})


class OpenAndReadRequest(BaseModel):
    url: str

@app.post("/browser/open-and-read", tags=["reading"])
async def open_and_read(body: OpenAndReadRequest) -> Any:
    """Open a URL in a new tab and immediately read its text."""
    return await _call_mcp_tool("browser_open_and_read", {"url": body.url})


# ── Interaction ───────────────────────────────────────────────────────────────

class ClickRequest(BaseModel):
    selector: str

class TypeRequest(BaseModel):
    selector: str
    text: str
    clear_first: bool = True

class KeyRequest(BaseModel):
    selector: str
    key: str

class JsRequest(BaseModel):
    script: str

@app.post("/browser/click", tags=["interaction"])
async def click(body: ClickRequest) -> Any:
    """Click an element by CSS selector."""
    return await _call_mcp_tool("browser_click", {"css_selector": body.selector})


@app.post("/browser/type", tags=["interaction"])
async def type_text(body: TypeRequest) -> Any:
    """Type text into an input field (CSS selector)."""
    return await _call_mcp_tool(
        "browser_type",
        {"css_selector": body.selector, "text": body.text, "clear_first": body.clear_first},
    )


@app.post("/browser/press-key", tags=["interaction"])
async def press_key(body: KeyRequest) -> Any:
    """Send a keyboard key to an element. key: ENTER | TAB | ESCAPE | …"""
    return await _call_mcp_tool(
        "browser_press_key", {"css_selector": body.selector, "key": body.key}
    )


@app.post("/browser/js", tags=["interaction"])
async def execute_js(body: JsRequest) -> Any:
    """Execute JavaScript in the current tab."""
    return await _call_mcp_tool("browser_execute_js", {"script": body.script})


# ── Search ────────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"query": "Babak EA github", "num_results": 5}]
    })
    query: str
    num_results: int = 5

class SearchAndReadRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"query": "Babak EA github", "result_index": 0, "use_duckduckgo": True}]
    })
    query: str
    result_index: int = 0
    use_duckduckgo: bool = False

class SearchAndAnalyzeRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "query": "Babak EA github",
            "analysis_question": "Who is this person and what AI projects have they built?"
        }]
    })
    query: str
    analysis_question: str = ""

@app.post("/search/google", tags=["search"])
async def google_search(body: SearchRequest) -> Any:
    """Search Google; returns titles, URLs, snippets."""
    return await _call_mcp_tool(
        "browser_google_search",
        {"query": body.query, "num_results": body.num_results},
    )


@app.post("/search/duckduckgo", tags=["search"])
async def ddg_search(body: SearchRequest) -> Any:
    """Search DuckDuckGo; returns titles, URLs, snippets."""
    return await _call_mcp_tool(
        "browser_ddg_search",
        {"query": body.query, "num_results": body.num_results},
    )


@app.post("/search/read", tags=["search"])
async def search_and_read(body: SearchAndReadRequest) -> Any:
    """Search → open result_index → return full page text."""
    return await _call_mcp_tool(
        "browser_search_and_read",
        {
            "query": body.query,
            "result_index": body.result_index,
            "use_duckduckgo": body.use_duckduckgo,
        },
    )


@app.post("/search/analyze", tags=["search + LLM"])
async def search_and_analyze(body: SearchAndAnalyzeRequest) -> Any:
    """Search → open top result → LLM analysis. One-shot power endpoint."""
    return await _call_mcp_tool(
        "search_and_analyze",
        {"query": body.query, "analysis_question": body.analysis_question},
    )


# ── LLM ──────────────────────────────────────────────────────────────────────

class LLMRequest(BaseModel):
    prompt: str
    system_prompt: str = ""
    model: str = ""

class AnalyzePageRequest(BaseModel):
    question: str
    system_prompt: str = ""

@app.post("/llm/ask", tags=["llm"])
async def ask_llm(body: LLMRequest) -> Any:
    """Send a prompt directly to LiteLLM."""
    return await _call_mcp_tool(
        "ask_llm",
        {"prompt": body.prompt, "system_prompt": body.system_prompt, "model": body.model},
    )


@app.get("/llm/models", tags=["llm"])
async def list_models() -> Any:
    """List models available on the LiteLLM proxy."""
    return await _call_mcp_tool("list_llm_models", {})


@app.post("/llm/analyze-page", tags=["llm"])
async def analyze_page(body: AnalyzePageRequest) -> Any:
    """Read current page and answer a question about it with the LLM."""
    return await _call_mcp_tool(
        "analyze_page",
        {"question": body.question, "system_prompt": body.system_prompt},
    )


# ── Research pipeline ────────────────────────────────────────────────────────

@app.post(
    "/research",
    tags=["research"],
    response_model=ResearchResponse,
    summary="Full research pipeline → Markdown report",
    response_description="Markdown report + LLM thoughts + step-by-step todo trace",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "Research a person — Babak EA": {
                            "summary": "Research a person (Babak EA)",
                            "description": "Search the internet for 'Babak EA', filter relevant pages, summarise each one and produce a full Markdown report.",
                            "value": {
                                "query": "Babak EA",
                                "topic": "Who is Babak EA — his AI career, projects, and online presence",
                                "top_n": 5,
                                "mcp_endpoint": "http://localhost:9040/mcp"
                            }
                        },
                        "Research a technology topic": {
                            "summary": "Research a technology topic",
                            "description": "Search and summarise the latest LLM models for video generation.",
                            "value": {
                                "query": "latest LLM models for video generation 2026",
                                "topic": "What are the most capable LLM/AI models for generating video in 2026?",
                                "top_n": 5
                            }
                        }
                    }
                }
            }
        }
    }
)
async def research(req: ResearchRequest) -> ResearchResponse:
    """
    ## Internet Research Pipeline

    Runs a full multi-step research job and returns a structured Markdown report.

    ### Steps (all visible in the response)
    1. **DDG search** — fetches up to 8 candidate pages from DuckDuckGo Lite
    2. **Fetch + relevance filter** — downloads each page and asks the LLM *"is this about the topic?"*
    3. **Per-page summary** — LLM writes a 3-5 sentence summary for each relevant page
    4. **Full report** — LLM synthesises all summaries into a structured Markdown document
    5. **Save** — report is written to `reports/<query>_<date>.md`

    ### Request fields
    | Field | Required | Description |
    |-------|----------|-------------|
    | `query` | ✅ | What to search for (e.g. `"Babak EA"`) |
    | `topic` | optional | Clarifies research focus for the LLM (defaults to `query`) |
    | `top_n` | optional | Max relevant pages to include (1-10, default **5**) |
    | `mcp_endpoint` | optional | Override the MCP server URL |

    ### Response fields
    | Field | Description |
    |-------|-------------|
    | `report_markdown` | Full Markdown report |
    | `report_saved_to` | File path where the report was saved |
    | `sources` | Per-URL: title, relevance verdict, LLM summary |
    | `todo_trace` | Step-by-step trace (DONE / SKIPPED / FAILED) |
    | `llm_thoughts` | Every LLM call with prompt preview + full response |
    """
    return _research_pipeline(req)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    print(f"[API Gateway] starting on {API_HOST}:{API_PORT}  →  MCP at {MCP_URL}")
    uvicorn.run("api_gateway:app", host=API_HOST, port=API_PORT, reload=False)
