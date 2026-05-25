import argparse
import shutil
import sys
import textwrap
import time
from pathlib import Path

import mss
import pyttsx3
import pytesseract
import win32api
import win32con
import win32gui
from PIL import Image
from screeninfo import get_monitors
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.edge.options import Options as EdgeOptions


def list_monitors():
    monitors = get_monitors()
    if not monitors:
        raise RuntimeError("No monitors were detected.")
    return monitors


def print_monitors(monitors):
    print("Detected monitors:")
    for index, monitor in enumerate(monitors, start=1):
        primary = " primary" if getattr(monitor, "is_primary", False) else ""
        print(
            f"  {index}. {monitor.width}x{monitor.height} at "
            f"({monitor.x}, {monitor.y}){primary}"
        )


def normalize_title(value):
    return " ".join((value or "").lower().split())


def get_active_window_details():
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    monitor_handle = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    monitor_info = win32api.GetMonitorInfo(monitor_handle)
    return {
        "hwnd": hwnd,
        "title": title,
        "rect": rect,
        "monitor_rect": monitor_info["Monitor"],
    }


def find_monitor_index_from_rect(monitors, rect):
    left, top, right, bottom = rect
    for index, monitor in enumerate(monitors, start=1):
        if (
            monitor.x == left
            and monitor.y == top
            and monitor.width == right - left
            and monitor.height == bottom - top
        ):
            return index
    return None


def build_driver(browser, port):
    debugger_address = f"127.0.0.1:{port}"
    if browser == "chrome":
        options = ChromeOptions()
        options.debugger_address = debugger_address
        return webdriver.Chrome(options=options)

    if browser == "edge":
        options = EdgeOptions()
        options.use_chromium = True
        options.add_experimental_option("debuggerAddress", debugger_address)
        return webdriver.Edge(options=options)

    raise ValueError(f"Unsupported browser: {browser}")


def get_tabs(driver):
    tabs = []
    original_handle = driver.current_window_handle
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        tabs.append(
            {
                "handle": handle,
                "title": driver.title.strip(),
                "url": driver.current_url.strip(),
            }
        )
    driver.switch_to.window(original_handle)
    return tabs


def pick_tab(driver, tabs, select_tab, monitor_index):
    active_window = get_active_window_details()
    active_title = normalize_title(active_window["title"])
    detected_monitor = None
    if monitor_index is not None:
        detected_monitor = find_monitor_index_from_rect(list_monitors(), active_window["monitor_rect"])

    if monitor_index is not None and detected_monitor is not None and detected_monitor != monitor_index:
        print(
            f"Warning: the foreground browser window looks like it is on monitor {detected_monitor}, "
            f"not monitor {monitor_index}."
        )

    suggested_handle = driver.current_window_handle
    if active_title:
        for tab in tabs:
            tab_title = normalize_title(tab["title"])
            if tab_title and (tab_title in active_title or active_title in tab_title):
                suggested_handle = tab["handle"]
                break

    if not select_tab:
        driver.switch_to.window(suggested_handle)
        return

    print("\nOpen tabs:")
    for index, tab in enumerate(tabs, start=1):
        marker = "*" if tab["handle"] == suggested_handle else " "
        print(f"  {index}. {marker} {tab['title'] or '(no title)'}")
        print(f"     {tab['url']}")

    raw_choice = input("\nSelect the tab number to read [default suggested]: ").strip()
    if not raw_choice:
        driver.switch_to.window(suggested_handle)
        return

    choice = int(raw_choice)
    if choice < 1 or choice > len(tabs):
        raise ValueError("Selected tab number is out of range.")
    driver.switch_to.window(tabs[choice - 1]["handle"])


def warm_entire_page(driver, pause=0.25, max_steps=40):
    original_scroll = driver.execute_script("return window.scrollY")
    driver.execute_script("window.scrollTo(0, 0)")
    last_height = 0

    for _ in range(max_steps):
        height = driver.execute_script(
            "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        )
        driver.execute_script("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 700))")
        time.sleep(pause)
        at_bottom = driver.execute_script(
            "return Math.ceil(window.scrollY + window.innerHeight) >= "
            "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight) - 4"
        )
        if at_bottom and height <= last_height:
            break
        last_height = height

    driver.execute_script("window.scrollTo(0, arguments[0])", original_scroll)


def expand_page_content(driver):
        script = """
        const clickableSelectors = [
            'button',
            '[role="button"]',
            'summary',
            '[aria-expanded="false"]',
            '[data-track-load="description_content_expand"]'
        ];

        const labels = ['more', 'show more', 'read more', 'expand', 'see more'];
        const nodes = Array.from(document.querySelectorAll(clickableSelectors.join(',')));
        let clicked = 0;

        for (const node of nodes) {
            const text = (node.innerText || node.textContent || node.getAttribute('aria-label') || '')
                .trim()
                .toLowerCase();
            const matches = labels.some((label) => text === label || text.startsWith(label + ' '));
            const collapsed = node.getAttribute('aria-expanded') === 'false';
            const likelyExpandControl = matches || collapsed || node.tagName.toLowerCase() === 'summary';

            if (!likelyExpandControl) {
                continue;
            }

            try {
                node.click();
                clicked += 1;
            } catch (error) {
                // Ignore elements that are not interactable.
            }
        }

        return clicked;
        """
        return driver.execute_script(script)


def extract_rendered_text(driver):
    script = """
    try {
      var candidates = Array.from(document.querySelectorAll('main, article, [role="main"]'));
      var target = null;
      for (var i = 0; i < candidates.length; i++) {
        if (candidates[i] && candidates[i].innerText && candidates[i].innerText.trim().length > 0) {
          target = candidates[i];
          break;
        }
      }
      if (!target) { target = document.body || document.documentElement; }
      if (!target) { return ''; }
      var text = (target.innerText || '');
      text = text.split('\\u00a0').join(' ');
      while (text.indexOf('\\n\\n\\n') !== -1) { text = text.split('\\n\\n\\n').join('\\n\\n'); }
      return text.trim();
    } catch (e) {
      try {
        var t = document.body ? document.body.innerText : (document.documentElement.innerText || '');
        t = t.split('\\u00a0').join(' ');
        while (t.indexOf('\\n\\n\\n') !== -1) { t = t.split('\\n\\n\\n').join('\\n\\n'); }
        return t.trim();
      } catch (e2) {
        return '';
      }
    }
    """
    return driver.execute_script(script)


def collect_page_text(driver):
    expand_page_content(driver)
    warm_entire_page(driver)
    expand_page_content(driver)
    text = extract_rendered_text(driver)
    if text:
        return text

    return driver.execute_script(
        "return (document.body && document.body.innerText ? document.body.innerText : '')"
        ".replace(/\\n{3,}/g, '\\n\\n').trim();"
    )


def browser_instructions(browser, port):
    browser_path = "chrome.exe" if browser == "chrome" else "msedge.exe"
    return textwrap.dedent(
        f"""
        Start {browser} with remote debugging enabled, then rerun this script.

        Example command:
          {browser_path} --remote-debugging-port={port} --user-data-dir=%TEMP%\\{browser}-debug

        Keep that browser window open, open the tab you want, then run this script again.
        """
    ).strip()


def read_browser_text(browser, port, monitor_index=None, select_tab=False):
    try:
        driver = build_driver(browser, port)
    except WebDriverException as exc:
        raise RuntimeError(browser_instructions(browser, port)) from exc

    try:
        tabs = get_tabs(driver)
        if not tabs:
            raise RuntimeError("The browser session has no open tabs.")

        pick_tab(driver, tabs, select_tab, monitor_index)
        text = collect_page_text(driver)
        return {
            "title": driver.title,
            "url": driver.current_url,
            "text": text,
        }
    finally:
        driver.quit()


def resolve_tesseract_path():
    if shutil.which("tesseract"):
        return

    common_path = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if common_path.exists():
        pytesseract.pytesseract.tesseract_cmd = str(common_path)
        return

    raise RuntimeError(
        "Tesseract OCR is not installed. Install it from https://github.com/tesseract-ocr/tesseract "
        "or put tesseract.exe on PATH."
    )


def capture_monitor_ocr(monitor_index):
    monitors = list_monitors()
    if monitor_index < 1 or monitor_index > len(monitors):
        raise ValueError("Monitor index is out of range.")

    resolve_tesseract_path()
    target = monitors[monitor_index - 1]
    region = {
        "left": target.x,
        "top": target.y,
        "width": target.width,
        "height": target.height,
    }

    with mss.mss() as sct:
        shot = sct.grab(region)

    image = Image.frombytes("RGB", shot.size, shot.rgb)
    grayscale = image.convert("L")
    text = pytesseract.image_to_string(grayscale)
    return {
        "title": f"Monitor {monitor_index}",
        "url": "screen-capture",
        "text": text.strip(),
    }


def save_output(path, content):
    output_path = Path(path)
    output_path.write_text(content, encoding="utf-8")
    print(f"Saved text to {output_path}")


def speak_text(content):
    if not content.strip():
        print("Nothing to speak.")
        return

    engine = pyttsx3.init()
    chunk_size = 3500
    for start in range(0, len(content), chunk_size):
        engine.say(content[start:start + chunk_size])
    engine.runAndWait()


def prompt_monitor(monitors):
    print_monitors(monitors)
    raw_value = input("Select monitor number: ").strip()
    if not raw_value:
        raise ValueError("A monitor number is required for OCR mode.")
    return int(raw_value)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Read the full text of a browser tab, including scrollable content. "
            "OCR mode is only a fallback for non-browser content."
        )
    )
    parser.add_argument("--mode", choices=["browser", "ocr"], default="browser")
    parser.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--monitor", type=int, help="1-based monitor number")
    parser.add_argument("--list-monitors", action="store_true")
    parser.add_argument(
        "--select-tab",
        action="store_true",
        help="Prompt to choose a browser tab instead of auto-selecting the active one.",
    )
    parser.add_argument("--save", help="Path to save the extracted text")
    parser.add_argument("--speak", action="store_true", help="Read the extracted text aloud")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    monitors = list_monitors()
    if args.list_monitors:
        print_monitors(monitors)
        if len(sys.argv) == 2:
            return

    if args.mode == "browser":
        result = read_browser_text(
            browser=args.browser,
            port=args.port,
            monitor_index=args.monitor,
            select_tab=args.select_tab,
        )
    else:
        monitor_index = args.monitor or prompt_monitor(monitors)
        result = capture_monitor_ocr(monitor_index)

    header = f"Title: {result['title']}\nSource: {result['url']}\n\n"
    full_output = header + result["text"]
    print(full_output)

    if args.save:
        save_output(args.save, full_output)

    if args.speak:
        speak_text(result["text"])


if __name__ == "__main__":
    main()