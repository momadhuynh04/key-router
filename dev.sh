#!/usr/bin/env bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo "========================================="
echo "   key-router - Development Mode"
echo "========================================="
echo ""

if [ ! -f "venv/bin/activate" ]; then
    echo -e "${RED}[!] Virtual environment not found (venv)!${NC}"
    echo -e "${RED}[!] Please run: python -m venv venv && source venv/bin/activate && pip install -r requirements.txt${NC}"
    exit 1
fi

cleanup() {
    echo ""
    echo -e "${CYAN}[*] Shutting down services...${NC}"
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

echo -e "${CYAN}[*] Starting Backend Proxy (Port 8082)...${NC}"
source venv/bin/activate
python -m cli.main &
BACKEND_PID=$!

echo -e "${YELLOW}[*] Starting Frontend Vite (Port 5173)...${NC}"
cd webui
npm run dev &
FRONTEND_PID=$!
cd ..

sleep 3

echo -e "${CYAN}[*] Opening WebUI Dev...${NC}"
if command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:5173 &>/dev/null
elif command -v open &>/dev/null; then
    open http://localhost:5173 &>/dev/null
else
    echo -e "${CYAN}[*] Please open http://localhost:5173 in your browser.${NC}"
fi

echo ""
echo "========================================="
echo -e "${GREEN}[DEV MODE RUNNING]${NC}"
echo " - Backend:  http://127.0.0.1:8082"
echo " - Frontend: http://localhost:5173"
echo "========================================="
echo "- Press Ctrl+C to stop all."

wait
