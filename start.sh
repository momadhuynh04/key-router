#!/usr/bin/env bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo "========================================="
echo "   key-router - Universal Proxy Server"
echo "========================================="
echo ""

if [ ! -f "venv/bin/activate" ]; then
    echo -e "${RED}[!] Virtual environment not found (venv)!${NC}"
    echo -e "${RED}[!] Please run: python -m venv venv && source venv/bin/activate && pip install -r requirements.txt${NC}"
    exit 1
fi

echo -e "${CYAN}[*] Activating Python virtual environment (venv)...${NC}"
source venv/bin/activate

cleanup() {
    echo ""
    echo -e "${CYAN}[*] Shutting down key-router...${NC}"
    kill $BACKEND_PID 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

echo -e "${CYAN}[*] Starting Proxy Server (FastAPI)...${NC}"
python -m cli.main &
BACKEND_PID=$!

sleep 2

echo -e "${CYAN}[*] Opening WebUI in browser...${NC}"
if command -v xdg-open &>/dev/null; then
    xdg-open http://127.0.0.1:8082 &>/dev/null
elif command -v open &>/dev/null; then
    open http://127.0.0.1:8082 &>/dev/null
else
    echo -e "${CYAN}[*] Please open http://127.0.0.1:8082 in your browser.${NC}"
fi

echo ""
echo "========================================="
echo -e "${GREEN}[OK] key-router is running successfully!${NC}"
echo "========================================="
echo "- Press Ctrl+C to stop the server."
echo ""

wait $BACKEND_PID
