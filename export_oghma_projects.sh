#!/usr/bin/env bash
# SCP oghma_projects from Windows OneDrive to benchmark/database_oghma/oghma_projects (WSL local).
#
# Faster than reading via /mnt/c because files traverse Windows OpenSSH, not DrvFS.
#
# Requires: OpenSSH client (WSL), OpenSSH Server on Windows, passwordless SSH or agent.
# Config: --config PATH only (required; no env / no file probe).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_OUT="$SCRIPT_DIR/oghma_projects"
CONFIG_PATH=""

to_remote_path() {
  local p="${1//\\//}"
  local user="${2:-}"
  local rest=""

  while [[ "$p" == *"//"* ]]; do
    p="${p//\/\//\/}"
  done

  if [[ "$p" =~ ^/([a-zA-Z])/(.*) ]]; then
    rest="${BASH_REMATCH[2]}"
  elif [[ "$p" =~ ^([A-Za-z]):/?(.*) ]]; then
    rest="${BASH_REMATCH[2]}"
  else
    echo "$p"
    return 0
  fi

  while [[ "$rest" == /* ]]; do
    rest="${rest#/}"
  done

  if [[ -n "$user" && "$rest" == Users/"$user"/* ]]; then
    echo "${rest#Users/$user/}"
  elif [[ "$rest" == AppData/* ]]; then
    echo "$rest"
  else
    echo "$rest"
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") --config PATH [--output PATH]

SCP oghma_projects from Windows OneDrive to database_oghma/oghma_projects.

Required config YAML keys:
  oghma_projects.source_dir
  oghma_projects.windows_ssh.host
  oghma_projects.windows_ssh.user

Options:
  --config PATH     Config YAML (required; only config channel)
  --output PATH     Local destination (default: benchmark/database_oghma/oghma_projects)
  -h, --help        Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --output)
      LOCAL_OUT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$CONFIG_PATH" ]]; then
  echo "error: --config is required" >&2
  usage >&2
  exit 1
fi
if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "error: config file not found: $CONFIG_PATH" >&2
  exit 1
fi

CFG_EXPORTS="$(
  python3 - "$CONFIG_PATH" <<'PY'
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
if not isinstance(cfg, dict):
    print("error: config root must be a mapping", file=sys.stderr)
    sys.exit(1)

ogp = cfg.get("oghma_projects")
if not isinstance(ogp, dict):
    print("error: config missing required mapping: oghma_projects", file=sys.stderr)
    sys.exit(1)

ssh = ogp.get("windows_ssh")
if not isinstance(ssh, dict):
    print("error: config missing required mapping: oghma_projects.windows_ssh", file=sys.stderr)
    sys.exit(1)

source_dir = ogp.get("source_dir")
host = ssh.get("host")
user = ssh.get("user")

def require_str(name, value):
    if not isinstance(value, str) or not value.strip():
        print(f"error: config missing required non-empty string: {name}", file=sys.stderr)
        sys.exit(1)
    return value.strip()

source_dir = require_str("oghma_projects.source_dir", source_dir)
host = require_str("oghma_projects.windows_ssh.host", host)
user = require_str("oghma_projects.windows_ssh.user", user)

def wsl_path_to_windows(path: str) -> str:
    import re
    s = path.replace("\\\\", "/").replace("\\", "/")
    m = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", s)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\\\")
        return f"{drive}:\\\\{rest}" if rest else f"{drive}:\\\\"
    return path

if source_dir.replace("\\", "/").startswith("/mnt/"):
    source_dir = wsl_path_to_windows(source_dir)
source_dir = source_dir.replace("\\", "/")

print(f"SOURCE_RAW={source_dir!r}")
print(f"WIN_HOST={host!r}")
print(f"WIN_USER={user!r}")
PY
)" || exit $?
eval "$CFG_EXPORTS"

SOURCE_REMOTE="$(to_remote_path "$SOURCE_RAW" "$WIN_USER")"

SSH_TARGET="${WIN_USER}@${WIN_HOST}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
SCP_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)

STAGE="$(mktemp -d)"
cleanup_stage() { rm -rf "$STAGE"; }
trap cleanup_stage EXIT

echo "==> Windows SSH target: $SSH_TARGET"
echo "==> Remote source (scp): $SOURCE_REMOTE"
echo "==> Local destination:   $LOCAL_OUT"

echo "==> Checking SSH connectivity..."
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "echo ok" >/dev/null

mkdir -p "$LOCAL_OUT"
echo "==> Downloading oghma_projects/ from Windows via scp (this may take several minutes)..."
scp "${SCP_OPTS[@]}" -r "${SSH_TARGET}:${SOURCE_REMOTE}/." "$STAGE/"

echo "==> Syncing into $LOCAL_OUT ..."
rsync -a --delete "$STAGE/." "$LOCAL_OUT/"

PROJECT_COUNT="$(find "$LOCAL_OUT" -mindepth 2 -maxdepth 2 -type d 2>/dev/null | wc -l)"
echo "==> Done: synced oghma_projects -> $LOCAL_OUT ($PROJECT_COUNT leaf projects)"
