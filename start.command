#!/usr/bin/env bash
# Finanz-Dashboard lokal starten (macOS): Doppelklick öffnet ein Terminal-Fenster.
# Beim ersten Mal: Rechtsklick → «Öffnen», weil die Datei aus dem Internet stammt.
# Zum Beenden: Einstellungen → «Dashboard beenden» oder dieses Fenster schliessen.
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  echo
  echo "  Python 3.9 oder neuer wurde nicht gefunden. Bitte von https://www.python.org/downloads/"
  echo "  installieren und danach start.command erneut öffnen."
  echo "  Python 3.9 or newer was not found. Please install it from https://www.python.org/downloads/."
  echo
  open "https://www.python.org/downloads/" 2>/dev/null
  read -r -p "Enter drücken zum Schliessen / press Enter to close … " _
  exit 1
fi
"$PY" app/launcher.py "$@" || read -r -p "Enter drücken zum Schliessen / press Enter to close … " _
