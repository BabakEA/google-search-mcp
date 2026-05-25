"""
search_api.py  —  Research Search FastAPI
==========================================
Wraps the research_report pipeline in a REST API.

Endpoints
---------
POST /search
    Runs the full DDG → fetch → LLM filter → summarise → report pipeline.
    Returns JSON with the markdown report, per-step thoughts, and a todo-style trace.

GET  /health
    Returns {"status": "ok"} with optional LLM + MCP connectivity check.

GET  /docs   (auto-generated Swagger UI)

Configuration (env vars)
------------------------
LITELLM_BASE_URL  – LiteLLM proxy   (default: http://localhost:4000)
LITELLM_API_KEY   – API key          (default: sk-litellm)
LITELLM_MODEL     – model alias      (default: my-agent)
MCP_ENDPOINT      – MCP server URL   (default: http://localhost:9040/mcp)
API_HOST          – bind host        (default: 0.0.0.0)
API_PORT          – bind port        (default: 9041)

Usage
-----
    python search_api.py
    uvicorn search_api:app --host 0.0.0.0 --port 9041 --reload

Then call:
    POST http://localhost:9041/search
    {
        "query": "Babak EA",
        "topic": "who is Babak EA and what has he built",
        "top_n": 5,
        "mcp_endpoint": "http://localhost:9040/mcp"   // optional override
    }
"""

from __future__ import annotations

import os
import re
import html as htmllib
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional

import requests as _requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import llm_client as _llm
import research_report as _rr

# ── config ────────────────────────────────────────────────────────────────────

DEFAULT_MCP_ENDPOINT = os.environ.get("MCP_ENDPOINT", "http://localhost:9040/mcp")
API_HOST             = os.environ.get("API_HOST", "0.0.0.0")
API_PORT             = int(os.environ.get("API_PORT", "9041"))

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Research Search API",
    description=(
        "Internet research pipeline: DDG search → page fetch → LLM relevance filter "
        "→ per-page summary → final Markdown report.\n\n"
        "Returns the report, full LLM reasoning trace, and a step-by-step todo log."
    ),
    version="1.0.0",
)

# ── request / response models ─────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(
        ...,
        description="What to search for on the internet.",
        examples=["Babak EA"],
    )
    topic: str = Field(
        default="",
        description=(
            "Optional clarification of what the research is about "
            "(used to focus the LLM relevance filter and report prompt). "
            "Defaults to the query itself if omitted."
        ),
        examples=["Who is Babak EA and what AI projects has he built?"],
    )
    top_n: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Number of relevant pages to include (1-10, default 5).",
    )
    mcp_endpoint: Optional[str] = Field(
        default=None,
        description=(
            "Override the MCP server endpoint for this request. "
            f"Falls back to the server default ({DEFAULT_MCP_ENDPOINT})."
        ),
        examples=["http://localhost:9040/mcp"],
    )


class TodoItem(BaseModel):
    step: int
    title: str
    status: str   # "done" | "skipped" | "failed"
    detail: str


class SourceResult(BaseModel):
    rank: int
    title: str
    url: str
    snippet: str
    relevant: bool
    llm_relevance_verdict: str   # raw YES/NO answer from LLM
    summary: str                 # per-page LLM summary


class SearchResponse(BaseModel):
    query: str
    topic: str
    mcp_endpoint: str
    total_candidates: int
    sources_kept: int
    report_markdown: str
    report_saved_to: str
    sources: List[SourceResult]
    todo_trace: List[TodoItem]
    llm_thoughts: List[Dict[str, Any]]   # chronological log of every LLM call


# ── pipeline (per-request, with trace capture) ─────────────────────────────────

def _run_pipeline(req: SearchRequest) -> SearchResponse:
    """Execute the full research pipeline and return a rich SearchResponse."""

    mcp_endpoint = req.mcp_endpoint or DEFAULT_MCP_ENDPOINT
    topic        = req.topic.strip() or req.query

    todo_trace:   List[TodoItem]       = []
    llm_thoughts: List[Dict[str, Any]] = []
    step = 0

    def _todo(title: str, status: str, detail: str = "") -> None:
        nonlocal step
        step += 1
        todo_trace.append(TodoItem(step=step, title=title, status=status, detail=detail))

    def _llm_call(label: str, prompt: str, system_prompt: str = "", **kwargs) -> str:
        """Wrapper that logs every LLM call and re-raises on error."""
        entry: Dict[str, Any] = {
            "call": label,
            "system_prompt": system_prompt,
            "prompt_preview": prompt[:1000] + ("…" if len(prompt) > 1000 else ""),
            "response": "",
            "error": None,
        }
        try:
            response = _llm.ask_llm(
                prompt,
                system_prompt=system_prompt or None,
                **kwargs,
            )
            entry["response"] = response
        except Exception as exc:
            entry["error"] = str(exc)
            llm_thoughts.append(entry)
            raise
        llm_thoughts.append(entry)
        return response

    # ── step 1: search ────────────────────────────────────────────────────────
    candidates = _rr.ddg_search(req.query, n=_rr.SEARCH_CANDIDATES)
    if not candidates:
        _todo("DDG search", "failed", "No results returned by DuckDuckGo Lite.")
        raise HTTPException(status_code=502, detail="No search results found for the given query.")
    _todo("DDG search", "done", f"Found {len(candidates)} candidates.")

    # ── step 2: fetch + LLM relevance filter ─────────────────────────────────
    source_results: List[SourceResult] = []
    relevant_pages: List[Dict]         = []

    for i, r in enumerate(candidates):
        if len(relevant_pages) >= req.top_n:
            break

        # fetch
        try:
            page_text = _rr.fetch_page_text(r["url"])
            _todo(f"Fetch [{i+1}] {r['url'][:60]}", "done", f"{len(page_text):,} chars")
        except Exception as exc:
            _todo(f"Fetch [{i+1}] {r['url'][:60]}", "skipped", str(exc))
            source_results.append(SourceResult(
                rank=i + 1, title=r["title"], url=r["url"], snippet=r["snippet"],
                relevant=False, llm_relevance_verdict="FETCH_ERROR", summary="",
            ))
            continue

        # relevance
        rel_prompt = (
            f"You are a relevance filter. The user searched for: \"{topic}\"\n\n"
            f"Page title  : {r['title']}\n"
            f"Page URL    : {r['url']}\n"
            f"Snippet     : {r['snippet']}\n"
            f"Page excerpt: {page_text[:800]}\n\n"
            f"Is this page genuinely about the topic/person the user searched for?\n"
            f"Answer ONLY with YES or NO — nothing else."
        )
        try:
            verdict = _llm_call(f"relevance [{i+1}]", rel_prompt).strip().upper()
        except Exception:
            verdict = "YES"   # default keep on error

        relevant = verdict.startswith("YES")
        _todo(
            f"Relevance check [{i+1}] {r['title'][:50]}",
            "done" if relevant else "skipped",
            f"LLM verdict: {verdict}",
        )

        if not relevant:
            source_results.append(SourceResult(
                rank=i + 1, title=r["title"], url=r["url"], snippet=r["snippet"],
                relevant=False, llm_relevance_verdict=verdict, summary="",
            ))
            continue

        relevant_pages.append({**r, "page_text": page_text})
        source_results.append(SourceResult(
            rank=i + 1, title=r["title"], url=r["url"], snippet=r["snippet"],
            relevant=True, llm_relevance_verdict=verdict, summary="",  # filled below
        ))

    if not relevant_pages:
        _todo("Filter result", "failed", "No relevant pages after filtering.")
        raise HTTPException(status_code=422, detail="No relevant pages found. Try a broader query.")
    _todo("Filter result", "done", f"Kept {len(relevant_pages)} of {len(candidates)} pages.")

    # ── step 3: per-page summaries ────────────────────────────────────────────
    summarised: List[Dict] = []
    rel_idx = 0   # index into source_results for relevant pages

    for p in relevant_pages:
        sum_prompt = (
            f"The user is researching: \"{topic}\"\n\n"
            f"Below is content scraped from:\n"
            f"Title : {p['title']}\n"
            f"URL   : {p['url']}\n\n"
            f"---\n{p['page_text']}\n---\n\n"
            f"Write a concise 3-5 sentence summary of what this page reveals about the topic/person, "
            f"highlighting any facts, projects, roles, skills, contacts, or notable details."
        )
        try:
            summary = _llm_call(
                f"summarise [{p['title'][:40]}]",
                sum_prompt,
                system_prompt="You are a precise research assistant. Be factual and specific.",
            )
            _todo(f"Summarise: {p['title'][:50]}", "done")
        except Exception as exc:
            summary = ""
            _todo(f"Summarise: {p['title'][:50]}", "failed", str(exc))

        summarised.append({"title": p["title"], "url": p["url"], "summary": summary})

        # back-fill summary into source_results
        for sr in source_results:
            if sr.url == p["url"]:
                sr.summary = summary
                break

    # ── step 4: build full report ─────────────────────────────────────────────
    sources_block = "\n".join(
        f"### Source {i+1}: {p['title']}\n**URL:** {p['url']}\n\n{p['summary']}\n"
        for i, p in enumerate(summarised)
    )
    report_prompt = (
        f"You are a professional research analyst. Based on the following summarised sources, "
        f"write a comprehensive, well-structured Markdown research report about: \"{topic}\"\n\n"
        f"The report MUST include:\n"
        f"1. **Overview** – one paragraph introducing the topic or person.\n"
        f"2. **Detailed Findings** – a section for each source (use the source title as a sub-heading) "
        f"   with a detailed paragraph about what that source reveals.\n"
        f"3. **Profile / Key Facts** – a bullet-point list with the most important facts, skills, "
        f"   projects, dates, roles, links, or highlights.\n"
        f"4. **Synthesis** – a concluding paragraph that ties everything together.\n"
        f"5. **Sources** – a numbered list of all URLs used.\n\n"
        f"Use proper Markdown formatting (##, ###, **, -, etc.).\n\n"
        f"--- SOURCES ---\n{sources_block}"
    )
    try:
        report_body = _llm_call(
            "build_report",
            report_prompt,
            system_prompt="You are a professional research analyst. Write in clear, formal English.",
            extra_body={"max_tokens": 2048},
        )
        _todo("Build final report", "done")
    except Exception as exc:
        _todo("Build final report", "failed", str(exc))
        raise HTTPException(status_code=500, detail=f"Report generation failed: {exc}")

    header = (
        f"# Research Report: {req.query}\n\n"
        f"**Generated:** {date.today().isoformat()}  \n"
        f"**Query:** {req.query}  \n"
        f"**Topic:** {topic}  \n"
        f"**MCP Endpoint:** {mcp_endpoint}  \n"
        f"**Sources analysed:** {len(summarised)}\n\n"
        f"---\n\n"
    )
    full_report = header + report_body

    # ── step 5: save ──────────────────────────────────────────────────────────
    saved_path = _rr.save_report(req.query, full_report)
    _todo("Save report to disk", "done", saved_path)

    return SearchResponse(
        query=req.query,
        topic=topic,
        mcp_endpoint=mcp_endpoint,
        total_candidates=len(candidates),
        sources_kept=len(summarised),
        report_markdown=full_report,
        report_saved_to=saved_path,
        sources=source_results,
        todo_trace=todo_trace,
        llm_thoughts=llm_thoughts,
    )


# ── routes ────────────────────────────────────────────────────────────────────

@app.post(
    "/search",
    response_model=SearchResponse,
    summary="Run a research pipeline and return a Markdown report",
    response_description="Full report + LLM thoughts + todo trace",
)
async def search(req: SearchRequest) -> SearchResponse:
    """
    Execute the full pipeline:
    1. Search DuckDuckGo Lite for **query**
    2. Fetch each page and ask the LLM if it is genuinely about **topic**
    3. Summarise each relevant page
    4. Synthesise a Markdown research report
    5. Save the report to `reports/` and return everything
    """
    return _run_pipeline(req)


@app.get(
    "/health",
    summary="Health check",
    response_description="Service status with optional connectivity checks",
)
async def health(check_llm: bool = False, check_mcp: bool = False) -> Dict[str, Any]:
    """
    Returns `{"status": "ok"}` plus optional connectivity checks.

    - `?check_llm=true`  – verifies the LiteLLM proxy is reachable
    - `?check_mcp=true`  – verifies the MCP server is reachable
    """
    result: Dict[str, Any] = {"status": "ok"}

    if check_llm:
        try:
            models = _llm.get_available_models()
            result["llm"] = {"status": "ok", "models": models}
        except Exception as exc:
            result["llm"] = {"status": "error", "detail": str(exc)}

    if check_mcp:
        mcp_url = DEFAULT_MCP_ENDPOINT
        try:
            r = _requests.get(mcp_url, timeout=5)
            result["mcp"] = {"status": "ok", "http_code": r.status_code, "url": mcp_url}
        except Exception as exc:
            result["mcp"] = {"status": "error", "detail": str(exc), "url": mcp_url}

    return result


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("search_api:app", host=API_HOST, port=API_PORT, reload=False)
