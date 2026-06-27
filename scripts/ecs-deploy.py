#!/usr/bin/env python3
"""Deploy agent-ops to imjson ECS via Aliyun Cloud Assistant."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from aliyunsdkcore.client import AcsClient
from aliyunsdkecs.request.v20140526 import DescribeInvocationResultsRequest, RunCommandRequest

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = os.environ.get("DEPLOY_INSTANCE", "i-bp18kchcnvcke6ltimn2")
REGION = "cn-hangzhou"
API_PORT = 9092

NGINX_PATCH_SCRIPT = """from pathlib import Path
path = Path("/etc/nginx/sites-enabled/api.imjson.cn")
text = path.read_text()
block = '''    # agent-ops-managed
    location /agent-ops/ {
        root /var/www;
        index index.html;
        try_files $uri $uri/ /agent-ops/index.html;
    }
    location /agent-ops/api/ {
        proxy_pass http://127.0.0.1:9092/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
    }
    # agent-ops-managed-end
'''
anchor = "    # tactile-app-managed"
if anchor not in text:
    raise SystemExit("nginx anchor not found")
path.write_text(text.replace(anchor, block + anchor, 1))
print("nginx updated")
"""


def run_remote(cmd: str, wait: int = 8) -> tuple[str, str]:
    client = AcsClient(os.environ["ALIYUN_ACCESS_KEY_ID"], os.environ["ALIYUN_ACCESS_KEY_SECRET"], REGION)
    req = RunCommandRequest.RunCommandRequest()
    req.set_InstanceIds([INSTANCE])
    req.set_CommandContent(cmd)
    req.set_Type("RunShellScript")
    req.set_accept_format("json")
    resp = json.loads(client.do_action_with_exception(req))
    invoke_id = resp["InvokeId"]
    time.sleep(wait)
    req2 = DescribeInvocationResultsRequest.DescribeInvocationResultsRequest()
    req2.set_InvokeId(invoke_id)
    req2.set_accept_format("json")
    r2 = json.loads(client.do_action_with_exception(req2))
    for item in r2.get("Invocation", {}).get("InvocationResults", {}).get("InvocationResult", []):
        out = item.get("Output", "")
        try:
            out = base64.b64decode(out).decode()
        except Exception:
            pass
        return item.get("InvocationStatus", ""), out
    return "Unknown", ""


def build_remote_script(db_password: str, jwt_secret: str, clone_url: str) -> str:
    db_password_esc = db_password.replace("'", "'\"'\"'")
    jwt_secret_esc = jwt_secret.replace("'", "'\"'\"'")
    nginx_b64 = base64.b64encode(NGINX_PATCH_SCRIPT.encode()).decode()
    return f"""#!/bin/bash
set -euo pipefail
export DATABASE_PASSWORD='{db_password_esc}'
export JWT_SECRET='{jwt_secret_esc}'
REMOTE_DIR=/opt/agent-ops
WEB_DIR=/var/www/agent-ops
API_PORT={API_PORT}
CLONE_URL='{clone_url}'

mkdir -p "$REMOTE_DIR" "$WEB_DIR"
if [ -d "$REMOTE_DIR/.git" ]; then
  cd "$REMOTE_DIR" && git fetch origin main && git reset --hard origin/main
else
  rm -rf "$REMOTE_DIR"/* "$REMOTE_DIR"/.[!.]* 2>/dev/null || true
  git clone "$CLONE_URL" "$REMOTE_DIR"
fi
cd "$REMOTE_DIR"

python3 -m venv .venv
. .venv/bin/activate
pip install -q -r backend/requirements.txt

cat > backend/.env << EOF
ENVIRONMENT=test
DATABASE_PASSWORD=$DATABASE_PASSWORD
JWT_SECRET=$JWT_SECRET
EOF

export PYTHONPATH=backend
cd backend
python -c "from app.database import ensure_schema; from app.models import Base; from app.database import engine; ensure_schema(); Base.metadata.create_all(bind=engine)"
python scripts/seed_data.py 200

cd "$REMOTE_DIR"
deactivate || true

cat > /etc/systemd/system/agent-ops-api.service << UNIT
[Unit]
Description=Agent Ops API test
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/agent-ops/backend
EnvironmentFile=/opt/agent-ops/backend/.env
Environment=PYTHONPATH=/opt/agent-ops/backend
ExecStart=/opt/agent-ops/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $API_PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable agent-ops-api
systemctl restart agent-ops-api
sleep 3
curl -sf http://127.0.0.1:$API_PORT/health || (journalctl -u agent-ops-api -n 40 --no-pager; exit 1)

cd "$REMOTE_DIR/frontend"
if [ ! -d node_modules ]; then npm ci || npm install; fi
npm run build
rm -rf "$WEB_DIR"/*
cp -r dist/* "$WEB_DIR/"
chown -R root:root "$WEB_DIR"

NGINX_CFG=/etc/nginx/sites-enabled/api.imjson.cn
if ! grep -q '# agent-ops-managed' "$NGINX_CFG"; then
  echo {nginx_b64} | base64 -d > /tmp/patch_nginx.py
  python3 /tmp/patch_nginx.py
  nginx -t && systemctl reload nginx
else
  echo "nginx agent-ops block exists"
  nginx -t && systemctl reload nginx
fi

echo DEPLOY_OK
curl -sf http://127.0.0.1:$API_PORT/health
"""


def main() -> None:
    db_password = os.environ.get("DATABASE_PASSWORD") or os.environ.get("CURSOR_DEV_DB_PASSWORD")
    if not db_password:
        print("ERROR: set DATABASE_PASSWORD or CURSOR_DEV_DB_PASSWORD", file=sys.stderr)
        sys.exit(1)

    jwt_secret = os.environ.get("JWT_SECRET", "agent-ops-test-jwt-secret-4918")
    gh_token = os.environ.get("github_access_token") or os.environ.get("GH_TOKEN", "")

    print("==> Push latest to GitHub")
    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=False)
    subprocess.run(
        ["git", "commit", "-m", "chore: deploy release", "--allow-empty"],
        cwd=ROOT,
        check=False,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=ROOT, check=True)

    clone_url = "https://github.com/mijunri/agent-ops.git"
    if gh_token:
        clone_url = f"https://{gh_token}@github.com/mijunri/agent-ops.git"

    remote_script = build_remote_script(db_password, jwt_secret, clone_url)
    encoded = base64.b64encode(remote_script.encode()).decode()
    cmd = f"echo {encoded} | base64 -d | bash"
    status, out = run_remote(cmd, wait=180)
    print(out)
    if status != "Success" or "DEPLOY_OK" not in out:
        print(f"Deploy failed: {status}", file=sys.stderr)
        sys.exit(1)
    print("==> Deploy success: http://118.31.57.25/agent-ops/")


if __name__ == "__main__":
    main()
