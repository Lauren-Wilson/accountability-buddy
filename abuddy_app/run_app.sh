#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PY="$REPO_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
	echo "Missing project virtual environment: $VENV_PY"
	echo "Create it with: python3 -m venv ../.venv"
	echo "Then run this script again."
	exit 1
fi

cd "$SCRIPT_DIR"

# Always install/update project dependencies in THIS venv.
"$VENV_PY" -m pip install -r requirements.txt >/dev/null

echo "Using Python: $("$VENV_PY" -c 'import sys; print(sys.executable)')"
echo "Using Streamlit: $("$VENV_PY" -m streamlit --version | head -n 1)"

# Launch Streamlit from the project venv to avoid Anaconda package conflicts.
exec "$VENV_PY" -m streamlit run app.py
