"""
Browser Automation MCP Server
==============================
Exposes browser automation via the Model Context Protocol.
No LLM bundled — your MCP client (Claude Desktop, etc.) provides the LLM.

Tools provided
--------------
Browser lifecycle:
    browser_launch          – start a headless (or visible) Chrome
    browser_connect         – attach to a running browser (remote debugging)
    browser_status          – check connection & current tab info
    browser_close           – quit the browser

Tab management:
    browser_list_tabs       – list all open tabs
    browser_switch_tab      – switch to a tab by index
    browser_close_tab       – close the current tab

Navigation:
    browser_navigate        – go to a URL in the current tab
    browser_open_new_tab    – open a URL in a brand new tab
    browser_go_back         – press the Back button

Reading content:
    browser_read_page       – scroll + expand + extract full page text
    browser_get_page_links  – list all hyperlinks on the current page
    browser_open_and_read   – open a URL in a new tab AND read its text

Interaction (type, click):
    browser_click           – click an element via CSS selector
    browser_click_xpath     – click an element via XPath
    browser_type            – type text into an input / textarea
    browser_press_key       – send a keyboard key (ENTER, TAB, etc.)
    browser_execute_js      – run arbitrary JavaScript

Search:
    browser_google_search   – search Google; return titles, URLs, snippets
    browser_ddg_search      – same but DuckDuckGo (fallback)
    browser_search_and_read – search → open top result → read full page

Transport
---------
Set env var MCP_TRANSPORT:
    stdio  (default) – for Claude Desktop / local use
    http             – for Docker / network use
                       MCP_HOST (default 0.0.0.0), MCP_PORT (default 9040)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from browser_automation import BrowserManager

# ── Read transport config from env vars at startup ────────────────────────────
_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio").lower()
_HOST      = os.environ.get("MCP_HOST",      "0.0.0.0")
_PORT      = int(os.environ.get("MCP_PORT",  "9040"))

# ── Server ────────────────────────────────────────────────────────────────────
# In MCP >= 1.x host/port/streamable_http_path are constructor args, not run() args.
mcp = FastMCP(
    "Browser Automation MCP",
    instructions=(
        "Automates a Chrome browser: navigate pages, read full text, "
        "type into fields, click elements, run Google searches, and open "
        "links in new tabs. No LLM required — your MCP client provides the LLM."
    ),
    host=_HOST,
    port=_PORT,
    streamable_http_path="/mcp",
)

# Single shared browser session (stateful across tool calls)
_browser = BrowserManager()


# ─────────────────────────────────────────────────────────────────────────────
# Browser lifecycle
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(structured_output=False)
def browser_launch(headless: bool = True) -> str:
    """
    Start a new Chrome browser instance.

    Args:
        headless: True = no visible window (required in Docker / server).
                  False = show the browser window (needs a desktop/display).
    """
    try:
        if _browser.is_ready:
            _browser.quit()
        if headless:
            _browser.launch_headless()
        else:
            _browser.launch_visible()
        info = _browser.get_current_info()
        return json.dumps({"status": "launched", "headless": headless, **info})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_connect(port: int = 9222, browser_type: str = "chrome") -> str:
    """
    Connect to an already-running browser that was started with remote debugging.

    Start the browser first, e.g.:
      chrome.exe --remote-debugging-port=9222 --user-data-dir=%TEMP%\\chrome-debug

    Args:
        port:         Remote-debugging port (default 9222).
        browser_type: "chrome" or "edge".
    """
    try:
        _browser.connect_to_browser(browser=browser_type, port=port)
        tabs = _browser.get_tabs()
        return json.dumps(
            {
                "status": "connected",
                "browser": browser_type,
                "port": port,
                "tab_count": len(tabs),
                "tabs": [{"index": i, "title": t["title"], "url": t["url"]}
                         for i, t in enumerate(tabs)],
            }
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_status() -> str:
    """Return whether the browser is connected plus current tab title and URL."""
    return json.dumps(_browser.get_current_info())


@mcp.tool(structured_output=False)
def browser_close() -> str:
    """Close the browser and release the WebDriver session."""
    _browser.quit()
    return json.dumps({"status": "closed"})


# ─────────────────────────────────────────────────────────────────────────────
# Tab management
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(structured_output=False)
def browser_list_tabs() -> str:
    """List all open browser tabs with their index, title, and URL."""
    try:
        tabs = _browser.get_tabs()
        return json.dumps(
            [{"index": i, "title": t["title"], "url": t["url"]}
             for i, t in enumerate(tabs)]
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_switch_tab(tab_index: int) -> str:
    """
    Switch to a specific tab.

    Args:
        tab_index: 0-based tab index. Use browser_list_tabs to see all tabs.
    """
    try:
        info = _browser.switch_tab(tab_index)
        return json.dumps({"status": "switched", **info})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_close_tab() -> str:
    """Close the currently active tab (switches to the previous tab)."""
    try:
        _browser.close_current_tab()
        return json.dumps({"status": "tab_closed", **_browser.get_current_info()})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Navigation
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(structured_output=False)
def browser_navigate(url: str) -> str:
    """
    Navigate the current tab to a URL.

    Args:
        url: Full URL including protocol, e.g. https://www.example.com
    """
    try:
        result = _browser.navigate(url)
        return json.dumps({"status": "navigated", **result})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_open_new_tab(url: str) -> str:
    """
    Open a URL in a brand-new tab and switch focus to it.

    Args:
        url: Full URL to open, e.g. https://www.wikipedia.org
    """
    try:
        result = _browser.open_new_tab(url)
        return json.dumps({"status": "opened_new_tab", **result})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_go_back() -> str:
    """Press the browser Back button on the current tab."""
    try:
        result = _browser.go_back()
        return json.dumps({"status": "went_back", **result})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Reading content
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(structured_output=False)
def browser_read_page() -> str:
    """
    Read the full visible text of the current tab.

    Scrolls through the entire page, expands collapsed sections (e.g. "Show more"),
    and returns all extracted text together with the page title, URL, and character count.
    Works on any website — articles, ChatGPT, docs, search results, etc.
    """
    try:
        result = _browser.read_page()
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_get_page_links(max_links: int = 30) -> str:
    """
    Get all hyperlinks on the current page.

    Args:
        max_links: Maximum number of links to return (default 30).
    """
    try:
        links = _browser.get_links(max_links=max_links)
        return json.dumps({"count": len(links), "links": links})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_open_and_read(url: str) -> str:
    """
    Open a URL in a new tab and immediately read its full text content.

    Combines browser_open_new_tab + browser_read_page into one step.
    Ideal for opening a search result and reading it.

    Args:
        url: The URL to open and read.
    """
    try:
        _browser.open_new_tab(url)
        result = _browser.read_page()
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Interaction — click, type, keys, JavaScript
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(structured_output=False)
def browser_click(css_selector: str) -> str:
    """
    Click an element on the current page using a CSS selector.

    Args:
        css_selector: CSS selector string.
          Examples:
            'button.submit'
            '#search-btn'
            'input[type="submit"]'
            'a[href*="login"]'
    """
    try:
        detail = _browser.click_element(css_selector)
        return json.dumps({"status": "clicked", "detail": detail})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_click_xpath(xpath: str) -> str:
    """
    Click an element on the current page using an XPath expression.

    Args:
        xpath: XPath string.
          Examples:
            '//button[contains(text(), "Submit")]'
            '//input[@name="q"]'
    """
    try:
        detail = _browser.click_element_xpath(xpath)
        return json.dumps({"status": "clicked", "detail": detail})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_type(css_selector: str, text: str, clear_first: bool = True) -> str:
    """
    Type text into an input field or textarea on the current page.

    Args:
        css_selector: CSS selector for the input element.
          Common examples:
            'input[name="q"]'        (Google search box)
            '#search-input'
            'textarea'
            'input[type="text"]'
        text:        The text to type.
        clear_first: Clear the field before typing (default True).
    """
    try:
        detail = _browser.type_text(css_selector, text, clear_first=clear_first)
        return json.dumps({"status": "typed", "detail": detail})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_press_key(css_selector: str, key: str) -> str:
    """
    Send a keyboard key to an element (e.g. submit a search form with ENTER).

    Args:
        css_selector: CSS selector for the target element.
        key:          Key name — one of:
                        ENTER, RETURN, TAB, ESCAPE, BACKSPACE, SPACE,
                        ARROW_DOWN, ARROW_UP
    """
    try:
        detail = _browser.press_key(css_selector, key)
        return json.dumps({"status": "key_pressed", "detail": detail})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_execute_js(script: str) -> str:
    """
    Execute arbitrary JavaScript in the current browser tab and return the result.

    Args:
        script: JavaScript code string.  The return value of the script is
                serialised and included in the response.
    """
    try:
        result = _browser.execute_javascript(script)
        return json.dumps({"status": "executed", "result": str(result)})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(structured_output=False)
def browser_google_search(query: str, num_results: int = 5) -> str:
    """
    Search Google and return structured results (title, URL, snippet).
    Opens the search in a new tab.

    Args:
        query:       Search query string.
        num_results: Number of results to return (default 5, max ~10).
    """
    try:
        results = _browser.google_search(query, num_results=num_results)
        return json.dumps(
            {"query": query, "result_count": len(results), "results": results}
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_ddg_search(query: str, num_results: int = 5) -> str:
    """
    Search DuckDuckGo and return structured results (title, URL, snippet).
    Use this as a fallback if Google search is blocked or unreliable.

    Args:
        query:       Search query string.
        num_results: Number of results to return (default 5).
    """
    try:
        results = _browser.duckduckgo_search(query, num_results=num_results)
        return json.dumps(
            {"query": query, "result_count": len(results), "results": results}
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(structured_output=False)
def browser_search_and_read(
    query: str,
    result_index: int = 0,
    use_duckduckgo: bool = False,
) -> str:
    """
    Search the web, open one result in a new tab, and return its full text.

    Args:
        query:           Search query.
        result_index:    Which result to open — 0 = top (default).
        use_duckduckgo:  Use DuckDuckGo instead of Google (default False).
    """
    try:
        results = (
            _browser.duckduckgo_search(query, num_results=result_index + 3)
            if use_duckduckgo
            else _browser.google_search(query, num_results=result_index + 3)
        )
        if not results:
            return json.dumps({"error": "No search results found.", "query": query})
        if result_index >= len(results):
            return json.dumps(
                {
                    "error": f"Only {len(results)} result(s) found; "
                             f"cannot open index {result_index}.",
                }
            )
        chosen = results[result_index]
        _browser.open_new_tab(chosen["url"])
        page = _browser.read_page()
        return json.dumps(
            {
                "query": query,
                "result_opened": chosen,
                "page_title": page["title"],
                "page_url": page["url"],
                "char_count": page["char_count"],
                "text": page["text"],
            }
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if _TRANSPORT == "http":
        print(
            f"[Browser Automation MCP] starting (HTTP) on {_HOST}:{_PORT}/mcp",
            file=sys.stderr,
        )
        mcp.run(transport="streamable-http")
    else:
        # stdio — default for Claude Desktop and most MCP clients
        mcp.run()
