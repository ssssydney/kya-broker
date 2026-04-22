#!/usr/bin/env bash
# KYA-Broker install script.
#
# Run once after cloning the repo into ~/.claude/skills/kya-broker.
# Idempotent: re-running upgrades deps and reinitialises the local ledger
# only if it doesn't already exist.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_ROOT="${KYA_BROKER_LOCAL:-$HOME/.claude/skills/kya-broker.local}"

echo ">> KYA-Broker install"
echo "   skill_root = ${SCRIPT_DIR}"
echo "   local_root = ${LOCAL_ROOT}"

# --- Python version gate ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "!! python3 not found on PATH. Install Python 3.11+ and retry." >&2
  exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=${PY_VER%.*}
PY_MINOR=${PY_VER#*.}
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  echo "!! Python >= 3.11 required, found ${PY_VER}" >&2
  exit 1
fi
echo "   python = ${PY_VER}"

# --- Install dependencies ---
echo ">> installing python deps"
if [ -d "${SCRIPT_DIR}/.venv" ]; then
  VENV_PY="${SCRIPT_DIR}/.venv/bin/python3"
else
  python3 -m venv "${SCRIPT_DIR}/.venv"
  VENV_PY="${SCRIPT_DIR}/.venv/bin/python3"
fi
"${VENV_PY}" -m pip install --upgrade pip setuptools wheel >/dev/null
"${VENV_PY}" -m pip install -e "${SCRIPT_DIR}" >/dev/null

# --- Wire the broker binary ---
mkdir -p "${HOME}/.local/bin"
cat > "${HOME}/.local/bin/broker" <<BROKER_WRAPPER
#!/usr/bin/env bash
exec "${VENV_PY}" -m src.cli "\$@"
BROKER_WRAPPER
chmod +x "${HOME}/.local/bin/broker"
echo "   installed broker -> ${HOME}/.local/bin/broker"

cat > "${HOME}/.local/bin/kya-broker-mcp" <<MCP_WRAPPER
#!/usr/bin/env bash
exec "${VENV_PY}" -m src.mcp_server "\$@"
MCP_WRAPPER
chmod +x "${HOME}/.local/bin/kya-broker-mcp"
echo "   installed kya-broker-mcp -> ${HOME}/.local/bin/kya-broker-mcp"

cat > "${HOME}/.local/bin/kya-broker-setup" <<SETUP_WRAPPER
#!/usr/bin/env bash
exec "${VENV_PY}" -m src.setup_wizard "\$@"
SETUP_WRAPPER
chmod +x "${HOME}/.local/bin/kya-broker-setup"
echo "   installed kya-broker-setup -> ${HOME}/.local/bin/kya-broker-setup"

# --- Init local state dir ---
mkdir -p "${LOCAL_ROOT}" "${LOCAL_ROOT}/dumps" "${LOCAL_ROOT}/logs"
if [ ! -f "${LOCAL_ROOT}/config.yaml" ]; then
  cp "${SCRIPT_DIR}/policy.default.yaml" "${LOCAL_ROOT}/config.yaml"
  echo "   seeded ${LOCAL_ROOT}/config.yaml from policy.default.yaml"
else
  echo "   ${LOCAL_ROOT}/config.yaml already exists, leaving as-is"
fi

if [ ! -f "${LOCAL_ROOT}/.env" ]; then
  cat > "${LOCAL_ROOT}/.env" <<EOF
# KYA-Broker secrets. Never commit this file.
# Uncomment + fill as needed.
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# VAST_API_KEY=optional-readonly-key-for-balance-check
EOF
  echo "   seeded ${LOCAL_ROOT}/.env (empty)"
fi

# --- Initialise the ledger ---
"${VENV_PY}" -c "from src.ledger import init_ledger; init_ledger()"
echo "   ledger at ${LOCAL_ROOT}/ledger.sqlite"

# --- MCP registration hint ---
echo ""
echo ">> Next steps:"
echo "   1. Add ~/.local/bin to PATH if it isn't already:"
echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
echo "   2. Register this skill with Claude Code:"
echo "        Claude Code reads ~/.claude/skills/<skill-name>/SKILL.md."
echo "        If the repo isn't at ~/.claude/skills/kya-broker, symlink it there."
echo "   3. Register the MCP server in Claude Code's config:"
cat <<JSON
        {
          "mcpServers": {
            "kya-broker": {
              "command": "${HOME}/.local/bin/kya-broker-mcp"
            }
          }
        }
JSON
echo "   4. Run the setup wizard:  broker setup"
echo ""
echo ">> install complete."
