#!/usr/bin/env bash
# Deploy GQQFM to remote machine ANQA (Val@192.168.0.133)

REMOTE="Val@192.168.0.133"
REMOTE_DIR="C:/WORK/GQQFM"
LOCAL_DIR="/c/WORK/GQQFM"
SSH_KEY="C:/Users/Val/.ssh/id_ed25519_claude"
SSH="ssh -i $SSH_KEY"
SCP="scp -i $SSH_KEY"

echo "=== Deploy GQQFM -> ANQA ==="

# 1. Create remote directory if needed
$SSH $REMOTE "mkdir -p $REMOTE_DIR" 2>/dev/null || true

# 2. Copy source files (exclude cache, docs, temp files)
echo ">> Copying source files..."
$SCP -r \
  "$LOCAL_DIR/data" \
  "$LOCAL_DIR/engine" \
  "$LOCAL_DIR/scoring" \
  "$LOCAL_DIR/templates" \
  "$LOCAL_DIR/ui" \
  "$LOCAL_DIR/tests" \
  "$LOCAL_DIR/config.py" \
  "$LOCAL_DIR/requirements.txt" \
  "$LOCAL_DIR/pyproject.toml" \
  "$REMOTE:$REMOTE_DIR/"

if [ $? -ne 0 ]; then
  echo "ERROR: scp failed"
  exit 1
fi

# 3. Install dependencies remotely (only if requirements.txt changed)
echo ">> Installing dependencies on remote..."
$SSH $REMOTE "C:/Users/Val/AppData/Local/Programs/Python/Python311/python.exe -m pip install -q -r $REMOTE_DIR/requirements.txt"

# 4. Restart Streamlit
echo ">> Restarting Streamlit on remote..."
$SSH $REMOTE "powershell -Command \"\$p = Get-NetTCPConnection -LocalPort 8501 -ErrorAction SilentlyContinue; if (\$p) { Stop-Process -Id \$p.OwningProcess -Force }\"" 2>/dev/null || true
sleep 2
$SSH $REMOTE "cd C:\\WORK\\GQQFM && C:\\Users\\Val\\AppData\\Local\\Programs\\Python\\Python311\\python.exe -m streamlit run ui/app.py --server.headless true --server.address 0.0.0.0 --server.port 8501" &
sleep 6

echo "=== Done ==="
echo "App: http://192.168.0.133:8501"
