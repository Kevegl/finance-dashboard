#!/usr/bin/env bash
# Finanz-Dashboard lokal starten (Linux, ohne Installation und ohne root).
# Zum Beenden: Einstellungen → «Dashboard beenden» oder Strg+C in diesem Fenster.
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  echo
  echo "  Python 3.9 oder neuer wurde nicht gefunden – bitte über die Paketverwaltung installieren,"
  echo "  z.B. 'sudo apt install python3'. / Python 3.9 or newer was not found – please install python3."
  echo
  exit 1
fi
exec "$PY" app/launcher.py "$@"
