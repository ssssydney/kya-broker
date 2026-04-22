#!/usr/bin/env bash
# Remove wrapper scripts and virtualenv. Does NOT touch user state in
# ~/.claude/skills/kya-broker.local/ — the ledger, config, and .env are the
# user's to preserve or delete.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_ROOT="${KYA_BROKER_LOCAL:-$HOME/.claude/skills/kya-broker.local}"

echo ">> KYA-Broker uninstall"

for name in broker kya-broker-mcp kya-broker-setup; do
  target="${HOME}/.local/bin/${name}"
  if [ -f "${target}" ]; then
    rm -f "${target}"
    echo "   removed ${target}"
  fi
done

if [ -d "${SCRIPT_DIR}/.venv" ]; then
  rm -rf "${SCRIPT_DIR}/.venv"
  echo "   removed ${SCRIPT_DIR}/.venv"
fi

echo ""
echo ">> user state preserved at:"
echo "   ${LOCAL_ROOT}"
echo ""
echo "   delete it manually if you also want to wipe ledger + config:"
echo "   rm -rf \"${LOCAL_ROOT}\""
echo ""
echo ">> uninstall complete."
