"""
Browser automation module for the MCP server.
Supports: headless launch (Docker), remote-debug connect, tab management,
navigation, full-page text extraction, link listing, click, type, Google search.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class BrowserManager:
    """
    Manages a Selenium WebDriver instance.

    Lifecycle:
        1. Call ``launch_headless()``  – start a new Chrome in headless mode (Docker / CI).
        OR
        1. Call ``connect_to_browser()`` – attach to a running browser that was
           started with ``--remote-debugging-port=9222``.
        2. Use the helper methods for navigation, reading, interaction, and search.
        3. Call ``quit()`` to release the session.
    """

    def __init__(self) -> None:
        self.driver: Optional[webdriver.Chrome] = None

    # ── Health check ──────────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        """Return True if a live driver session exists."""
        try:
            if self.driver is None:
                return False
            # any DOM call will throw if the session is dead
            _ = self.driver.current_url
            return True
        except Exception:
            self.driver = None
            return False

    def _require_driver(self) -> webdriver.Chrome:
        if not self.is_ready:
            raise RuntimeError(
                "Browser is not connected. "
                "Call browser_launch() or browser_connect() first."
            )
        return self.driver  # type: ignore[return-value]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def launch_headless(self, extra_args: Optional[List[str]] = None) -> None:
        """Start a new headless Chrome (no display required – works in Docker)."""
        options = ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        if extra_args:
            for arg in extra_args:
                options.add_argument(arg)
        # Selenium Manager (bundled with selenium >= 4.6) auto-downloads ChromeDriver
        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def launch_visible(self) -> None:
        """Start Chrome with a visible window (requires a display / desktop)."""
        options = ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        self.driver = webdriver.Chrome(options=options)

    def connect_to_browser(self, browser: str = "chrome", port: int = 9222) -> None:
        """Attach to an existing browser started with ``--remote-debugging-port``."""
        debugger_address = f"127.0.0.1:{port}"
        if browser.lower() == "chrome":
            options = ChromeOptions()
            options.debugger_address = debugger_address
            self.driver = webdriver.Chrome(options=options)
        elif browser.lower() == "edge":
            options = EdgeOptions()
            options.use_chromium = True
            options.add_experimental_option("debuggerAddress", debugger_address)
            self.driver = webdriver.Edge(options=options)
        else:
            raise ValueError(f"Unsupported browser: {browser!r}. Use 'chrome' or 'edge'.")

    def quit(self) -> None:
        """Close the browser and release the WebDriver session."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # ── Status ────────────────────────────────────────────────────────────────

    def get_current_info(self) -> Dict[str, Any]:
        if not self.is_ready:
            return {"status": "not_connected", "title": "", "url": "", "tab_count": 0}
        return {
            "status": "connected",
            "title": self.driver.title,  # type: ignore[union-attr]
            "url": self.driver.current_url,  # type: ignore[union-attr]
            "tab_count": len(self.driver.window_handles),  # type: ignore[union-attr]
        }

    # ── Tab management ────────────────────────────────────────────────────────

    def get_tabs(self) -> List[Dict[str, str]]:
        """Return info for every open tab (switches back to the original after)."""
        d = self._require_driver()
        original = d.current_window_handle
        tabs: List[Dict[str, str]] = []
        for handle in d.window_handles:
            d.switch_to.window(handle)
            tabs.append(
                {
                    "handle": handle,
                    "title": d.title.strip() or "(no title)",
                    "url": d.current_url,
                }
            )
        d.switch_to.window(original)
        return tabs

    def switch_tab(self, tab_index: int) -> Dict[str, str]:
        d = self._require_driver()
        handles = d.window_handles
        if tab_index < 0 or tab_index >= len(handles):
            raise IndexError(
                f"Tab index {tab_index} is out of range (0–{len(handles) - 1})."
            )
        d.switch_to.window(handles[tab_index])
        return {"title": d.title, "url": d.current_url}

    def close_current_tab(self) -> None:
        d = self._require_driver()
        d.close()
        # Switch to last remaining tab if any
        if d.window_handles:
            d.switch_to.window(d.window_handles[-1])

    # ── Navigation ────────────────────────────────────────────────────────────

    def navigate(self, url: str, wait_seconds: float = 2.0) -> Dict[str, str]:
        d = self._require_driver()
        d.get(url)
        time.sleep(wait_seconds)
        return {"title": d.title, "url": d.current_url}

    def open_new_tab(self, url: str, wait_seconds: float = 2.5) -> Dict[str, str]:
        d = self._require_driver()
        d.execute_script("window.open(arguments[0], '_blank');", url)
        d.switch_to.window(d.window_handles[-1])
        time.sleep(wait_seconds)
        return {"title": d.title, "url": d.current_url}

    def go_back(self) -> Dict[str, str]:
        d = self._require_driver()
        d.back()
        time.sleep(1)
        return {"title": d.title, "url": d.current_url}

    # ── Page content extraction ───────────────────────────────────────────────

    def _scroll_full_page(self, pause: float = 0.3, max_steps: int = 40) -> None:
        d = self._require_driver()
        d.execute_script("window.scrollTo(0, 0)")
        last_height = 0
        for _ in range(max_steps):
            height = d.execute_script(
                "return Math.max("
                "  document.body.scrollHeight,"
                "  document.documentElement.scrollHeight"
                ")"
            )
            d.execute_script(
                "window.scrollBy(0, Math.max(window.innerHeight * 0.9, 700))"
            )
            time.sleep(pause)
            at_bottom = d.execute_script(
                "return Math.ceil(window.scrollY + window.innerHeight) >= "
                "Math.max(document.body.scrollHeight,"
                "         document.documentElement.scrollHeight) - 4"
            )
            if at_bottom and height <= last_height:
                break
            last_height = height
        d.execute_script("window.scrollTo(0, 0)")

    def _expand_collapsed_sections(self) -> int:
        d = self._require_driver()
        script = """
        const labels = ['more', 'show more', 'read more', 'expand', 'see more'];
        const nodes = Array.from(document.querySelectorAll(
            'button, [role="button"], summary, [aria-expanded="false"]'
        ));
        let clicked = 0;
        for (const node of nodes) {
            const text = (
                node.innerText ||
                node.textContent ||
                node.getAttribute('aria-label') || ''
            ).trim().toLowerCase();
            const collapsed = node.getAttribute('aria-expanded') === 'false';
            const matches = labels.some(l => text === l || text.startsWith(l + ' '));
            if (matches || collapsed || node.tagName.toLowerCase() === 'summary') {
                try { node.click(); clicked++; } catch (e) {}
            }
        }
        return clicked;
        """
        return d.execute_script(script)

    def _extract_text_js(self) -> str:
        d = self._require_driver()
        script = """
        try {
            var target = null;
            var candidates = Array.from(
                document.querySelectorAll('main, article, [role="main"]')
            );
            for (var el of candidates) {
                if (el.innerText && el.innerText.trim().length > 200) {
                    target = el; break;
                }
            }
            if (!target) target = document.body || document.documentElement;
            var text = target.innerText || '';
            text = text.replace(/\\u00a0/g, ' ');
            while (text.includes('\\n\\n\\n')) text = text.replace(/\\n{3,}/g, '\\n\\n');
            return text.trim();
        } catch (e) {
            return (document.body && document.body.innerText) || '';
        }
        """
        return d.execute_script(script) or ""

    def read_page(self) -> Dict[str, Any]:
        """
        Full page extraction: expand collapsed sections, scroll, then extract text.
        Returns title, url, text, char_count.
        """
        d = self._require_driver()
        self._expand_collapsed_sections()
        self._scroll_full_page()
        self._expand_collapsed_sections()
        text = self._extract_text_js()
        if not text:
            text = (
                d.execute_script(
                    "return (document.body && document.body.innerText"
                    "  ? document.body.innerText : '')"
                    "  .replace(/\\n{3,}/g, '\\n\\n').trim();"
                )
                or ""
            )
        return {
            "title": d.title,
            "url": d.current_url,
            "text": text,
            "char_count": len(text),
        }

    def get_links(self, max_links: int = 50) -> List[Dict[str, str]]:
        """Return up to ``max_links`` <a href> links from the current page."""
        d = self._require_driver()
        elements = d.find_elements(By.TAG_NAME, "a")[: max_links * 4]
        seen: set = set()
        links: List[Dict[str, str]] = []
        for el in elements:
            try:
                href = el.get_attribute("href") or ""
                text = (el.text or "").strip()
                if href.startswith("http") and href not in seen:
                    seen.add(href)
                    links.append({"text": text or "(no text)", "url": href})
                    if len(links) >= max_links:
                        break
            except Exception:
                continue
        return links

    # ── Interaction ───────────────────────────────────────────────────────────

    def click_element(self, css_selector: str, timeout: int = 10) -> str:
        d = self._require_driver()
        element = WebDriverWait(d, timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector))
        )
        element.click()
        time.sleep(0.5)
        return f"Clicked: {css_selector}"

    def click_element_xpath(self, xpath: str, timeout: int = 10) -> str:
        d = self._require_driver()
        element = WebDriverWait(d, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
        element.click()
        time.sleep(0.5)
        return f"Clicked XPath: {xpath}"

    def type_text(
        self, css_selector: str, text: str, clear_first: bool = True, timeout: int = 10
    ) -> str:
        d = self._require_driver()
        element = WebDriverWait(d, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )
        if clear_first:
            element.clear()
        element.send_keys(text)
        time.sleep(0.3)
        return f"Typed into: {css_selector}"

    def press_key(self, css_selector: str, key_name: str, timeout: int = 10) -> str:
        _KEY_MAP: Dict[str, str] = {
            "ENTER": Keys.ENTER,
            "RETURN": Keys.RETURN,
            "TAB": Keys.TAB,
            "ESCAPE": Keys.ESCAPE,
            "BACKSPACE": Keys.BACKSPACE,
            "SPACE": Keys.SPACE,
            "ARROW_DOWN": Keys.ARROW_DOWN,
            "ARROW_UP": Keys.ARROW_UP,
        }
        d = self._require_driver()
        key = _KEY_MAP.get(key_name.upper(), key_name)
        element = WebDriverWait(d, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )
        element.send_keys(key)
        time.sleep(0.3)
        return f"Pressed {key_name} on: {css_selector}"

    def execute_javascript(self, script: str) -> Any:
        d = self._require_driver()
        return d.execute_script(script)

    def get_element_text(self, css_selector: str, timeout: int = 10) -> str:
        d = self._require_driver()
        element = WebDriverWait(d, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
        )
        return element.text

    # ── Google search ─────────────────────────────────────────────────────────

    def google_search(self, query: str, num_results: int = 5) -> List[Dict[str, str]]:
        """
        Open Google search in a new tab and return structured results.

        Returns a list of dicts: {title, url, snippet}.
        """
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://www.google.com/search?q={encoded}&hl=en&num={num_results + 5}"
        self.open_new_tab(search_url)
        time.sleep(2)
        d = self._require_driver()
        # Extract via JS for reliability across Google layout changes
        results: List[Dict[str, str]] = d.execute_script(
            """
            const out = [];
            const limit = arguments[0];
            // Try modern result containers
            const containers = document.querySelectorAll(
                'div.g, div[data-hveid], div[jscontroller]'
            );
            for (const c of containers) {
                const h3 = c.querySelector('h3');
                const a  = c.querySelector('a[href]');
                if (!h3 || !a) continue;
                const title = h3.innerText.trim();
                const url   = a.href || '';
                if (!url.startsWith('http') || url.includes('google.com/search')) continue;
                const snippetEl = c.querySelector(
                    '[data-sncf], .VwiC3b, .s3v9rd, [style*="-webkit-line-clamp"]'
                );
                const snippet = snippetEl
                    ? snippetEl.innerText.trim().slice(0, 300)
                    : '';
                out.push({ title, url, snippet });
                if (out.length >= limit) break;
            }
            return out;
            """,
            num_results,
        )
        return results or []

    def duckduckgo_search(self, query: str, num_results: int = 5) -> List[Dict[str, str]]:
        """
        Fallback search using DuckDuckGo (more scraping-friendly).
        Returns a list of dicts: {title, url, snippet}.
        """
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://duckduckgo.com/?q={encoded}&ia=web"
        self.open_new_tab(search_url)
        time.sleep(3)
        d = self._require_driver()
        results: List[Dict[str, str]] = d.execute_script(
            """
            const out = [];
            const limit = arguments[0];
            const articles = document.querySelectorAll('article[data-testid="result"]');
            for (const a of articles) {
                const titleEl   = a.querySelector('h2 a');
                const snippetEl = a.querySelector('[data-result="snippet"]');
                if (!titleEl) continue;
                const title   = titleEl.innerText.trim();
                const url     = titleEl.href || '';
                const snippet = snippetEl ? snippetEl.innerText.trim().slice(0, 300) : '';
                out.push({ title, url, snippet });
                if (out.length >= limit) break;
            }
            return out;
            """,
            num_results,
        )
        return results or []
