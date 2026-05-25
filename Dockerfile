# ─────────────────────────────────────────────────────────────────────────────
# Google Search MCP Server — Dockerfile
#
# Self-contained image:
#   • Python 3.11
#   • Google Chrome (stable, headless — no display needed)
#   • Selenium (auto-downloads matching ChromeDriver via Selenium Manager)
#   • MCP server on port 9040 (streamable-http)
#
# NO LLM inside the image — your MCP client provides the intelligence.
#
# Build:
#   docker build -t yourdockerhubuser/google-search-mcp:latest .
#
# Run:
#   docker run -p 9040:9040 yourdockerhubuser/google-search-mcp:latest
#
# Override port at build time:
#   docker build --build-arg MCP_PORT=9040 -t google-search-mcp .
#
# Or use docker-compose:
#   docker compose up
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# ── System deps + Google Chrome ───────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        gnupg \
        curl \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libc6 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libexpat1 \
        libfontconfig1 \
        libgbm1 \
        libgcc-s1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libstdc++6 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxrandr2 \
        libxrender1 \
        libxss1 \
        libxtst6 \
        lsb-release \
        xdg-utils \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
        http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
COPY requirements_mcp.txt .
RUN pip install --no-cache-dir -r requirements_mcp.txt

# ── Pre-cache ChromeDriver via Selenium Manager ───────────────────────────────
# Runs Chrome once at build time so the driver is downloaded into the image
# and the first real request is fast.
RUN python - <<'EOF'
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
try:
    d = webdriver.Chrome(options=opts)
    d.quit()
    print("ChromeDriver cached successfully.")
except Exception as e:
    print(f"ChromeDriver pre-cache warning: {e}")
EOF


# ── Application code ──────────────────────────────────────────────────────────

COPY mcp_server.py browser_automation.py README.md LICENSE ./
# ── Build-time ARG → ENV (users can override with --build-arg) ────────────────
# Override example:
#   docker build --build-arg MCP_PORT=9040 -t browser-automation-mcp .
ARG MCP_TRANSPORT=http
ARG MCP_HOST=0.0.0.0
ARG MCP_PORT=9040

ENV MCP_TRANSPORT=${MCP_TRANSPORT} \
    MCP_HOST=${MCP_HOST} \
    MCP_PORT=${MCP_PORT}

EXPOSE ${MCP_PORT}

CMD ["python", "mcp_server.py"]
