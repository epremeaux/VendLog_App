#!/bin/bash
# Starts VendLog and opens it in your browser. Close this window (or press Ctrl+C) to stop.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Return to close..." _
  exit 1
fi

# A private virtual environment avoids changing the system Python.
if [ ! -x .venv/bin/python ]; then
  echo "Setting up VendLog (first run only)..."
  python3 -m venv .venv || { echo "Could not create the virtual environment."; read -r -p "Press Return to close..." _; exit 1; }
fi

if ! .venv/bin/python -c "import flask" >/dev/null 2>&1; then
  echo "Installing required packages..."
  .venv/bin/python -m pip install -r requirements.txt || { echo "Package install failed."; read -r -p "Press Return to close..." _; exit 1; }
fi

.venv/bin/python app.py
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "VendLog could not start. See the messages above."
  read -r -p "Press Return to close..." _
fi
exit $status
