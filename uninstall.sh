#!/usr/bin/env bash
# Remove the broker wrapper + venv.
# Does NOT touch user state in ~/.claude/skills/kya-broker.local/ — that's the
# user's ledger and they decide whether to keep it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_ROOT="${KYA_BROKER_LOCAL:-$HOME/.claude/skills/kya-broker.local}"

echo ">> KYA-Broker v1.0 uninstall"

if [ -f "${HOME}/.local/bin/broker" ]; then
  rm -f "${HOME}/.local/bin/broker"
  echo "   removed ${HOME}/.local/bin/broker"
fi

if [ -d "${SCRIPT_DIR}/.venv" ]; then
  rm -rf "${SCRIPT_DIR}/.venv"
  echo "   removed ${SCRIPT_DIR}/.venv"
fi

echo ""
echo ">> user state preserved at:"
echo "   ${LOCAL_ROOT}"
echo ""
echo "   delete it manually if you want to wipe ledger + budget:"
echo "   rm -rf \"${LOCAL_ROOT}\""
echo ""
echo ">> uninstall complete."
