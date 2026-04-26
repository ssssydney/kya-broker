#!/usr/bin/env bash
# v1.0 install. Sets up venv + 'broker' CLI wrapper.
# Idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_ROOT="${KYA_BROKER_LOCAL:-$HOME/.claude/skills/kya-broker.local}"

# Wrappers under here will reference SCRIPT_DIR's venv.
export KYA_BROKER_HOME="${KYA_BROKER_HOME:-$SCRIPT_DIR}"

echo ">> KYA-Broker v1.0 install"
echo "   skill_root = ${SCRIPT_DIR}"
echo "   local_root = ${LOCAL_ROOT}"

# Python version gate
if ! command -v python3 >/dev/null 2>&1; then
  echo "!! python3 not found on PATH. Install Python 3.11+ first." >&2
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

# venv
echo ">> installing python deps"
if [ ! -d "${SCRIPT_DIR}/.venv" ]; then
  python3 -m venv "${SCRIPT_DIR}/.venv"
fi
VENV_PY="${SCRIPT_DIR}/.venv/bin/python3"
"${VENV_PY}" -m pip install --upgrade pip setuptools wheel >/dev/null
"${VENV_PY}" -m pip install -e "${SCRIPT_DIR}" >/dev/null

# CLI wrapper
mkdir -p "${HOME}/.local/bin"
cat > "${HOME}/.local/bin/broker" <<BROKER_WRAPPER
#!/usr/bin/env bash
exec "${VENV_PY}" -m src.cli "\$@"
BROKER_WRAPPER
chmod +x "${HOME}/.local/bin/broker"
echo "   installed broker -> ${HOME}/.local/bin/broker"

# Local state dir
mkdir -p "${LOCAL_ROOT}"
"${VENV_PY}" -c "from src.ledger import init_ledger; init_ledger()"
echo "   ledger at ${LOCAL_ROOT}/ledger.sqlite"

echo ""
echo ">> install complete."
echo "   Try: broker --version"
