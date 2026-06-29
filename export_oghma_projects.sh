#!/usr/bin/env bash
# SCP oghma_projects from Windows OneDrive to database/og/oghma_projects (WSL local).
#
# Faster than reading via /mnt/c because files traverse Windows OpenSSH, not DrvFS.
#
# Requires: OpenSSH client (WSL), OpenSSH Server on Windows, passwordless SSH or agent.
#
# Config: --config PATH (parent update_all supplies config.yaml).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_OUT="$SCRIPT_DIR/oghma_projects"

detect_windows_host_ip() {
  local wsl_ip="" gw_ip="" lan_ip="" adapter="" line ip

  while IFS= read -r line; do
    line="${line//$'\r'/}"
    if [[ "$line" == *adapter* ]]; then
      adapter="$line"
    fi
    if [[ "$line" =~ IPv4[^:]*:[[:space:]]*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+) ]]; then
      ip="${BASH_REMATCH[1]}"
      if [[ "$adapter" == *WSL* ]]; then
        wsl_ip="$ip"
      fi
      if [[ "$ip" != 127.* && "$ip" != 169.254.* && "$ip" != 198.18.* \
            && "$adapter" != *VMware* && "$adapter" != *Loopback* ]]; then
        if [[ -z "$lan_ip" ]]; then
          lan_ip="$ip"
        fi
      fi
    fi
  done < <(cmd.exe /c "chcp 65001>nul & ipconfig" 2>/dev/null | sed 's/\r$//')

  gw_ip="$(ip route show default 2>/dev/null | awk '{print $3; exit}' || true)"

  if [[ -n "$wsl_ip" ]]; then
    echo "$wsl_ip"
  elif [[ -n "$gw_ip" && "$gw_ip" != 127.* ]]; then
    echo "$gw_ip"
  elif [[ -n "$lan_ip" ]]; then
    echo "$lan_ip"
  else
    echo ""
  fi
}

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

CONFIG_PATH="${SIMULATION_DATABASE_CONFIG:-}"

if [[ -z "$CONFIG_PATH" || ! -f "$CONFIG_PATH" ]]; then
  for candidate in "$SCRIPT_DIR/../config.yaml" "$SCRIPT_DIR/../config.example.yaml"; do
    if [[ -f "$candidate" ]]; then
      CONFIG_PATH="$candidate"
      break
    fi
  done
fi

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

SCP oghma_projects from Windows OneDrive to database/og/oghma_projects.

Options:
  --config PATH     Config YAML (oghma_projects.* / virtuallab.windows_ssh.*)
  --output PATH     Local destination (default: database/og/oghma_projects)
  -h, --help        Show this help

Environment:
  WINDOWS_SSH_HOST, WINDOWS_SSH_USER, OGHMA_PROJECTS_SOURCE_DIR
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

load_config() {
  if [[ ! -f "$CONFIG_PATH" ]]; then
    return 0
  fi
  # shellcheck disable=SC2046
  eval "$(
    python3 - "$CONFIG_PATH" <<'PY'
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
ogp = cfg.get("oghma_projects") or {}
vl = cfg.get("virtuallab") or {}
ssh = ogp.get("windows_ssh") or vl.get("windows_ssh") or {}

def emit(name, value):
    if value is None or value == "":
        return
    print(f"export {name}={value!r}")

def wsl_path_to_windows(path: str) -> str:
    import re
    s = path.replace("\\\\", "/").replace("\\", "/")
    m = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", s)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\\\")
        return f"{drive}:\\\\{rest}" if rest else f"{drive}:\\\\"
    return path

def normalize_windows_path(path: str) -> str:
    return path.replace("\\", "/")

source_dir = ogp.get("source_dir")
if isinstance(source_dir, str):
    if source_dir.replace("\\", "/").startswith("/mnt/"):
        source_dir = wsl_path_to_windows(source_dir)
    source_dir = normalize_windows_path(source_dir)

emit("OGHMA_PROJECTS_SOURCE_DIR", source_dir)
emit("WINDOWS_SSH_HOST", ssh.get("host"))
emit("WINDOWS_SSH_USER", ssh.get("user"))
PY
  )"
}

load_config

WIN_USER="${WINDOWS_SSH_USER:-${USER}}"
if [[ -z "${WINDOWS_SSH_HOST:-}" || "${WINDOWS_SSH_HOST}" == "auto" ]]; then
  WINDOWS_SSH_HOST="$(detect_windows_host_ip)"
  if [[ -n "${WINDOWS_SSH_HOST}" ]]; then
    echo "==> Detected Windows host IP via cmd.exe ipconfig: $WINDOWS_SSH_HOST"
  fi
fi
WIN_HOST="${WINDOWS_SSH_HOST:-}"
SOURCE_RAW="${OGHMA_PROJECTS_SOURCE_DIR:-C:/Users/${WIN_USER}/OneDrive/oghma_projects}"
SOURCE_REMOTE="$(to_remote_path "$SOURCE_RAW" "$WIN_USER")"

if [[ -z "$WIN_HOST" ]]; then
  echo "error: WINDOWS_SSH_HOST not set and could not detect Windows host IP via cmd.exe ipconfig" >&2
  echo "hint: set windows_ssh.host in config.yaml or export WINDOWS_SSH_HOST=<ip>" >&2
  exit 1
fi

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
