#!/usr/bin/env bash
# build_and_push.sh
# ──────────────────────────────────────────────────────────────────────────────
# Build the Google Search MCP Docker image and (optionally) push it to
# Docker Hub.
#
# Usage:
#   ./build_and_push.sh                        # build only (local tag)
#   ./build_and_push.sh --push                 # build + push to Docker Hub
#   DOCKER_USER=yourname ./build_and_push.sh --push
#
# Environment variables you can override:
#   DOCKER_USER   Your Docker Hub username          (default: aiforest)
#   IMAGE_NAME    Repository name on Docker Hub     (default: google-search-mcp)
#   IMAGE_TAG     Tag to apply                      (default: latest)
#   MCP_PORT      Port baked into the image         (default: 9040)
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
DOCKER_USER="${DOCKER_USER:-aiforest}"
IMAGE_NAME="${IMAGE_NAME:-google-search-mcp}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
MCP_PORT="${MCP_PORT:-9040}"

FULL_IMAGE="${DOCKER_USER}/${IMAGE_NAME}:${IMAGE_TAG}"
PUSH=false

for arg in "$@"; do
  [[ "$arg" == "--push" ]] && PUSH=true
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo "════════════════════════════════════════════════════════════"
echo "  Google Search MCP — Docker build"
echo "  Image : ${FULL_IMAGE}"
echo "  Port  : ${MCP_PORT}"
echo "  Push  : ${PUSH}"
echo "════════════════════════════════════════════════════════════"

# ── Build ─────────────────────────────────────────────────────────────────────
docker build \
  --build-arg MCP_PORT="${MCP_PORT}" \
  --build-arg MCP_TRANSPORT=http \
  --build-arg MCP_HOST=0.0.0.0 \
  --tag "${FULL_IMAGE}" \
  --tag "${DOCKER_USER}/${IMAGE_NAME}:$(date +%Y%m%d)" \
  .

echo ""
echo "✔  Build complete: ${FULL_IMAGE}"



  echo ""
  echo "To test locally:"
  echo "  docker run --rm -p ${MCP_PORT}:${MCP_PORT} --shm-size=2g ${FULL_IMAGE}"
fi
