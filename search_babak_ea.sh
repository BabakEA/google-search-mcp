#!/usr/bin/env bash
# search_babak_ea.sh
# Search DuckDuckGo Lite for a query and print top N results.
# Usage:
#   bash search_babak_ea.sh
#   bash search_babak_ea.sh "Babak EA trading bot"
#   bash search_babak_ea.sh "" 6

QUERY="${1:-Babak EA github}"
MAX="${2:-4}"

echo "──────────────────────────────────────────────────────────────"
echo " Query : $QUERY"
echo " Top   : $MAX results  (via DuckDuckGo Lite)"
echo "──────────────────────────────────────────────────────────────"

python - "$QUERY" "$MAX" << 'PYEOF'
import sys, re, html as htmllib, urllib.parse, urllib.request, textwrap

query       = sys.argv[1]
max_results = int(sys.argv[2])

encoded = urllib.parse.quote_plus(query)
req = urllib.request.Request(
    f"https://lite.duckduckgo.com/lite/?q={encoded}",
    headers={"User-Agent": "Lynx/2.9.0 libwww-FM/2.14"}
)
with urllib.request.urlopen(req, timeout=15) as resp:
    raw = resp.read().decode("utf-8", errors="ignore")

def clean(s):
    s = re.sub(r'<[^>]+>', '', s)
    return htmllib.unescape(s.strip())

def decode_ddg_url(u):
    m = re.search(r'uddg=([^&"]+)', u)
    if m:
        return urllib.parse.unquote(m.group(1))
    return u

# DDG Lite puts href before class in the <a> tag
link_pat    = re.compile(r'''<a\s[^>]*href=["']([^"']+)["'][^>]*class=["']result-link["'][^>]*>(.*?)</a>''', re.DOTALL)
snippet_pat = re.compile(r"<td class=['\"]result-snippet['\"][^>]*>(.*?)</td>", re.DOTALL)

links    = link_pat.findall(raw)
snippets = [clean(m.group(1)) for m in snippet_pat.finditer(raw)]

found = 0
for i, (raw_url, title) in enumerate(links):
    if found >= max_results:
        break
    url = decode_ddg_url(raw_url)
    found += 1
    snippet = snippets[i] if i < len(snippets) else ""
    print(f"\n[{found}] {clean(title)}")
    print(f"    URL     : {url}")
    if snippet:
        print(f"    Snippet : {textwrap.shorten(snippet, width=120)}")

if found == 0:
    print("\nNo results found. Try a different query or check your internet connection.")
PYEOF

echo ""
echo "──────────────────────────────────────────────────────────────"
echo " Done."
echo "──────────────────────────────────────────────────────────────"
