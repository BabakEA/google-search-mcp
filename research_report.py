"""
research_report.py
──────────────────
Research pipeline:
  1. Search DuckDuckGo Lite for a query (top 8 candidates)
  2. Fetch each page and ask the LLM whether it is relevant to the topic
  3. Keep the top 5 relevant pages, ask the LLM to summarise each one
  4. Ask the LLM to synthesise everything into a detailed Markdown report
  5. Save the report to  reports/<sanitized-query>_<date>.md

Usage:
    python research_report.py "Babak EA"
    python research_report.py "Latest LLM models for video generation" 6
"""

from __future__ import annotations

import os
import re
import sys
import html as htmllib
import textwrap
import urllib.parse
import urllib.request
from datetime import date
from typing import Optional

from llm_client import ask_llm

# ── tuning knobs ──────────────────────────────────────────────────────────────

SEARCH_CANDIDATES  = 8    # how many DDG results to fetch before filtering
MAX_RELEVANT       = 5    # keep at most this many relevant pages
PAGE_CHAR_LIMIT    = 4000 # chars per page (keeps each LLM call reasonable)

# ── helpers ───────────────────────────────────────────────────────────────────

def ddg_search(query: str, n: int = SEARCH_CANDIDATES) -> list[dict]:
    """Return up to *n* results from DuckDuckGo Lite."""
    encoded = urllib.parse.quote_plus(query)
    req = urllib.request.Request(
        f"https://lite.duckduckgo.com/lite/?q={encoded}",
        headers={"User-Agent": "Lynx/2.9.0 libwww-FM/2.14"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    def clean(s: str) -> str:
        return htmllib.unescape(re.sub(r"<[^>]+>", "", s).strip())

    def decode_url(u: str) -> str:
        m = re.search(r"uddg=([^&\"]+)", u)
        return urllib.parse.unquote(m.group(1)) if m else u

    link_pat    = re.compile(r'''<a\s[^>]*href=["']([^"']+)["'][^>]*class=["']result-link["'][^>]*>(.*?)</a>''', re.DOTALL)
    snippet_pat = re.compile(r"<td class=['\"]result-snippet['\"][^>]*>(.*?)</td>", re.DOTALL)
    links    = link_pat.findall(raw)
    snippets = [clean(m.group(1)) for m in snippet_pat.finditer(raw)]

    results = []
    for i, (raw_url, title) in enumerate(links):
        if len(results) >= n:
            break
        results.append({
            "title":   clean(title),
            "url":     decode_url(raw_url),
            "snippet": snippets[i] if i < len(snippets) else "",
        })
    return results


def fetch_page_text(url: str, char_limit: int = PAGE_CHAR_LIMIT) -> str:
    """Fetch a URL and return clean plain text up to *char_limit* chars."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    for tag in ("script", "style", "svg", "nav", "footer", "head"):
        raw = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = htmllib.unescape(text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:char_limit]


def is_relevant(query: str, title: str, url: str, snippet: str, page_text: str) -> bool:
    """Ask the LLM whether the page is truly about the query topic."""
    prompt = (
        f"You are a relevance filter. The user searched for: \"{query}\"\n\n"
        f"Page title  : {title}\n"
        f"Page URL    : {url}\n"
        f"Snippet     : {snippet}\n"
        f"Page excerpt: {page_text[:800]}\n\n"
        f"Is this page genuinely about the topic/person the user searched for?\n"
        f"Answer ONLY with YES or NO — nothing else."
    )
    try:
        answer = ask_llm(prompt).strip().upper()
        return answer.startswith("YES")
    except Exception:
        return True   # default: keep the page if the LLM call fails


def summarise_page(query: str, title: str, url: str, page_text: str) -> str:
    """Ask the LLM to produce a 3-5 sentence summary of the page relative to the query."""
    prompt = (
        f"The user is researching: \"{query}\"\n\n"
        f"Below is content scraped from:\n"
        f"Title : {title}\n"
        f"URL   : {url}\n\n"
        f"---\n{page_text}\n---\n\n"
        f"Write a concise 3-5 sentence summary of what this page reveals about the topic/person, "
        f"highlighting any facts, projects, roles, skills, contacts, or notable details."
    )
    return ask_llm(prompt, system_prompt="You are a precise research assistant. Be factual and specific.")


def build_report(query: str, pages: list[dict]) -> str:
    """
    Ask the LLM to write a full Markdown research report from the aggregated
    per-page summaries.  *pages* is a list of dicts with keys:
        title, url, summary
    """
    sources_block = "\n".join(
        f"### Source {i+1}: {p['title']}\n**URL:** {p['url']}\n\n{p['summary']}\n"
        for i, p in enumerate(pages)
    )

    prompt = (
        f"You are a professional research analyst. Based on the following summarised sources, "
        f"write a comprehensive, well-structured Markdown research report about: \"{query}\"\n\n"
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
    return ask_llm(
        prompt,
        system_prompt="You are a professional research analyst. Write in clear, formal English.",
        extra_body={"max_tokens": 2048},
    )


def save_report(query: str, markdown: str) -> str:
    """Save the report to reports/<slug>_<date>.md and return the file path."""
    os.makedirs("reports", exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
    fname = f"reports/{slug}_{date.today().isoformat()}.md"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(markdown)
    return fname


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    query      = sys.argv[1] if len(sys.argv) > 1 else "Babak EA"
    max_keep   = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_RELEVANT

    SEP = "─" * 62

    print(f"\n{SEP}")
    print(f"  RESEARCH REPORT GENERATOR")
    print(f"  Query : {query!r}   │   Max sources : {max_keep}")
    print(f"{SEP}")

    # ── 1. Search ─────────────────────────────────────────────────────────────
    print(f"\n[1/4] Searching DuckDuckGo Lite …")
    candidates = ddg_search(query, n=SEARCH_CANDIDATES)
    if not candidates:
        print("  No results found. Aborting.")
        return
    print(f"  Found {len(candidates)} candidates.")

    # ── 2. Fetch + relevance filter ───────────────────────────────────────────
    print(f"\n[2/4] Fetching pages and filtering for relevance …")
    relevant_pages: list[dict] = []

    for i, r in enumerate(candidates):
        if len(relevant_pages) >= max_keep:
            break
        print(f"  [{i+1}/{len(candidates)}] {r['url'][:70]} …", end=" ", flush=True)

        try:
            page_text = fetch_page_text(r["url"])
        except Exception as exc:
            print(f"SKIP (fetch error: {exc})")
            continue

        if not is_relevant(query, r["title"], r["url"], r["snippet"], page_text):
            print("SKIP (not relevant)")
            continue

        print("OK")
        relevant_pages.append({**r, "page_text": page_text})

    if not relevant_pages:
        print("  No relevant pages found after filtering. Aborting.")
        return
    print(f"  Kept {len(relevant_pages)} relevant page(s).")

    # ── 3. Summarise each page ────────────────────────────────────────────────
    print(f"\n[3/4] Summarising each page with LLM …")
    summarised: list[dict] = []

    for i, p in enumerate(relevant_pages):
        print(f"  [{i+1}/{len(relevant_pages)}] {p['title'][:60]} …", end=" ", flush=True)
        try:
            summary = summarise_page(query, p["title"], p["url"], p["page_text"])
            summarised.append({"title": p["title"], "url": p["url"], "summary": summary})
            print("done")
        except Exception as exc:
            print(f"SKIP (LLM error: {exc})")

    if not summarised:
        print("  All LLM summarisation calls failed. Aborting.")
        return

    # ── 4. Build full report ──────────────────────────────────────────────────
    print(f"\n[4/4] Generating full Markdown report …")
    try:
        report_md = build_report(query, summarised)
    except Exception as exc:
        print(f"  Report generation failed: {exc}")
        return

    # Prepend metadata header
    header = (
        f"# Research Report: {query}\n\n"
        f"**Generated:** {date.today().isoformat()}  \n"
        f"**Query:** {query}  \n"
        f"**Sources analysed:** {len(summarised)}\n\n"
        f"---\n\n"
    )
    full_report = header + report_md

    # Save + print
    path = save_report(query, full_report)
    print(f"  Report saved → {path}\n")
    print(SEP)
    print(full_report)
    print(SEP)


if __name__ == "__main__":
    main()
