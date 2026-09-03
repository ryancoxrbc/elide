#!/usr/bin/env bash
# Launch the Claims Processor against a claim folder.
#   ./run.sh                  # uses the current directory
#   ./run.sh /path/to/claim   # uses that folder
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
# Treat a leading argument as the claim folder only if it is not a flag, so
# "./run.sh --port 9000" still defaults the folder to the current directory.
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
  FOLDER="$1"; shift
else
  FOLDER="$PWD"
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating virtual environment..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"
fi

# The claim folder is the working directory, so point Python at the package.
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"

exec "$VENV/bin/python" -m claims_processor.app "$FOLDER" "$@"
