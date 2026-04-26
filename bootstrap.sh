#!/usr/bin/env bash
# v1.0 bootstrap. Idempotent; safe to re-run.
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"

set -euo pipefail

OPT_DIR="${KYA_BROKER_HOME:-$HOME/.local/opt/kya-broker}"
LOCAL_DIR="${KYA_BROKER_LOCAL:-$HOME/.claude/skills/kya-broker.local}"
REPO_URL="https://github.com/ssssydney/kya-broker.git"

BLUE="\033[34m"; GREEN="\033[32m"; YELLOW="\033[33m"; RESET="\033[0m"
say() { printf "${BLUE}>>${RESET} %s\n" "$*"; }
ok()  { printf "${GREEN}ok${RESET} %s\n" "$*"; }
warn(){ printf "${YELLOW}!!${RESET} %s\n" "$*"; }

say "kya-broker v1.0 bootstrap"
say "  opt_dir   = $OPT_DIR"
say "  local_dir = $LOCAL_DIR"

# Python gate
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required (Python >= 3.11). Install from python.org first." >&2
  exit 1
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=${PY_VER%.*}; PY_MINOR=${PY_VER#*.}
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  echo "Python >= 3.11 required, found $PY_VER" >&2
  exit 1
fi
ok "python $PY_VER"

# Clone or update
if [ -d "$OPT_DIR/.git" ]; then
  say "repo already cloned — pulling latest"
  (cd "$OPT_DIR" && git pull --ff-only --quiet) && ok "updated" || warn "pull failed (working tree may have local changes)"
else
  mkdir -p "$(dirname "$OPT_DIR")"
  say "cloning $REPO_URL"
  git clone --depth 1 --quiet "$REPO_URL" "$OPT_DIR"
  ok "cloned"
fi

# Install (creates venv + wires CLI wrappers)
say "running install.sh"
bash "$OPT_DIR/install.sh"

# Done
echo
say "next steps"
echo "  1.  Add ~/.local/bin to PATH (if not already):"
echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
echo "  2.  (Optional) Set spending caps:"
echo "        broker budget --daily 50 --monthly 500"
echo "  3.  Make sure 'Claude for Chrome' extension is installed + signed in"
echo "        (chromewebstore.google.com → search 'Claude for Chrome')"
echo "  4.  Save the v1.0 SKILL.md to your skill dir:"
echo "        mkdir -p ~/.claude/skills/kya-broker"
echo "        curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/SKILL.md \\"
echo "          -o ~/.claude/skills/kya-broker/SKILL.md"
echo
ok "bootstrap complete."
