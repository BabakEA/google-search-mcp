"""
llm_demo.py
───────────
Demo pipeline:
  1. Search DuckDuckGo Lite for "Babak EA github"
  2. Print top 4 results
  3. Fetch the GitHub profile page (or first GitHub URL)
  4. Ask the LLM to summarise it

Usage:
    python llm_demo.py
    python llm_demo.py "Babak EA witcher" 2       # custom query, open result #2
"""

from __future__ import annotations

import re
import sys
import html as htmllib
import textwrap
import urllib.parse
import urllib.request
from typing import Optional

from llm_client import ask_llm

# ── helpers ───────────────────────────────────────────────────────────────────

def ddg_search(query: str, max_results: int = 4) -> list[dict]:
    """Return top results from DuckDuckGo Lite as list of {title, url, snippet}."""
    encoded = urllib.parse.quote_plus(query)
    req = urllib.request.Request(
        f"https://lite.duckduckgo.com/lite/?q={encoded}",
        headers={"User-Agent": "Lynx/2.9.0 libwww-FM/2.14"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    def clean(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s)
        return htmllib.unescape(s.strip())

    def decode_url(u: str) -> str:
        m = re.search(r"uddg=([^&\"]+)", u)
        return urllib.parse.unquote(m.group(1)) if m else u

    link_pat    = re.compile(r'''<a\s[^>]*href=["']([^"']+)["'][^>]*class=["']result-link["'][^>]*>(.*?)</a>''', re.DOTALL)
    snippet_pat = re.compile(r"<td class=['\"]result-snippet['\"][^>]*>(.*?)</td>", re.DOTALL)

    links    = link_pat.findall(raw)
    snippets = [clean(m.group(1)) for m in snippet_pat.finditer(raw)]

    results = []
    for i, (raw_url, title) in enumerate(links):
        if len(results) >= max_results:
            break
        results.append({
            "title":   clean(title),
            "url":     decode_url(raw_url),
            "snippet": snippets[i] if i < len(snippets) else "",
        })
    return results


def fetch_page_text(url: str, char_limit: int = 8000) -> str:
    """Fetch a URL and return its plain text (stripped HTML), up to char_limit chars."""
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

    # Strip <script>, <style>, <svg> blocks first
    for tag in ("script", "style", "svg", "nav", "footer"):
        raw = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = htmllib.unescape(text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:char_limit]


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    query       = sys.argv[1] if len(sys.argv) > 1 else "Babak EA github"
    open_result = int(sys.argv[2]) if len(sys.argv) > 2 else 1   # 1-based index

    SEP = "─" * 62

    # ── Step 1: Search ────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f" STEP 1 › Searching DuckDuckGo Lite for: {query!r}")
    print(SEP)

    results = ddg_search(query, max_results=4)
    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        print(f"\n[{i}] {r['title']}")
        print(f"    URL     : {r['url']}")
        if r["snippet"]:
            print(f"    Snippet : {textwrap.shorten(r['snippet'], width=110)}")

    # ── Step 2: Pick a URL to open ────────────────────────────────────────────
    idx = min(open_result, len(results)) - 1
    target = results[idx]

    print(f"\n{SEP}")
    print(f" STEP 2 › Fetching page #{idx+1}: {target['url']}")
    print(SEP)

    try:
        page_text = fetch_page_text(target["url"])
        print(f"  Fetched {len(page_text):,} characters of page content.")
    except Exception as exc:
        print(f"  Could not fetch page: {exc}")
        page_text = f"Title: {target['title']}\nSnippet: {target['snippet']}"

    # ── Step 3: Ask LLM to summarise ─────────────────────────────────────────
    print(f"\n{SEP}")
    print(f" STEP 3 › Asking LLM to summarise the page …")
    print(SEP)

    prompt = (
        f"The following is the text content scraped from this web page:\n"
        f"URL: {target['url']}\n\n"
        f"---BEGIN CONTENT---\n{page_text}\n---END CONTENT---\n\n"
        f"Please provide a concise summary (5-8 bullet points) covering:\n"
        f"• Who or what this page is about\n"
        f"• Key projects, skills, or highlights mentioned\n"
        f"• Any notable contact or social links\n"
        f"Keep it factual and brief."
    )

    try:
        summary = ask_llm(prompt, system_prompt="You are a helpful research assistant. Summarise web page content clearly and concisely.")
        print(f"\n{summary}\n")
    except Exception as exc:
        print(f"  LLM call failed: {exc}")

    print(SEP)
    print(" Done.")
    print(SEP)


if __name__ == "__main__":
    main()
