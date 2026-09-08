#!/usr/bin/env bash
set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "========================================="
echo "  key-router - IDE Detection Refresh"
echo "========================================="
echo ""

RESPONSE=$(curl -s http://127.0.0.1:8082/api/ide-detect-refresh 2>/dev/null)

if [ -z "$RESPONSE" ]; then
    echo -e "${RED}[!] Server not running at http://127.0.0.1:8082${NC}"
    echo -e "${RED}[!] Start the proxy first: ./start.sh or uvicorn proxy.server:app${NC}"
    exit 1
fi

echo -e "${GREEN}[OK] Detection refreshed!${NC}"
echo ""

DETECTED=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin)['detected']; [print(f'  \033[0;36m{k}\033[0m - {v[\"name\"]} ({v[\"version\"]})') for k,v in d.items()]" 2>/dev/null)

if [ -n "$DETECTED" ]; then
    echo "$DETECTED"
else
    echo -e "${CYAN}  No IDEs detected on this system.${NC}"
fi

echo ""
