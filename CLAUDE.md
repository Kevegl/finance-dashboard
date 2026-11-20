# Arbeitsanweisungen für Claude – Finanz-Dashboard

Selbst gehostetes Portfolio-Dashboard (Relai, IBKR, Finpension, VIAC) für ein Proxmox-Homelab.
Sprache im Projekt: Deutsch (Schweizer Schreibweise, „ss“ statt „ß“), UI zweisprachig DE/EN.

## GitHub ist die einzige Quelle
- Repository: `Kevegl/finance-dashboard`, Branch `main`. Keine Zips erstellen oder verteilen.
- Zu Beginn jeder Sitzung das Repo hinzufügen (`add_repo`, Zugriff `push`) und klonen; immer auf dem aktuellen `main` arbeiten.
- Jede Änderung als eigener, nachvollziehbarer Commit mit deutscher Commit-Nachricht
  (Zusammenfassung + Stichpunkte), Autor `Kevegl <Kevegl@users.noreply.github.com>`, danach pushen.
- Direkt auf `main` committen und pushen, auch wenn die Sitzung einen anderen Arbeits-Branch vorgibt.
- Nicht direkt auf `main` force-pushen und keine Historie umschreiben – das Update im Container akzeptiert nur Fast-Forwards.

## Auslieferung
- Der Container `finance` (Debian-LXC) ist per `git clone` installiert (`/root/finance-dashboard`, Deploy Key, nur lesend).
- Updates installiert der Benutzer im Dashboard unter *Einstellungen → Updates* (Dienst `finance-dashboard-update`).
  Fallback im Container: `cd /root/finance-dashboard && git pull && bash install.sh`.
- Nach einem Push dem Benutzer kurz sagen, was sich ändert und dass er es über *Updates* installieren kann.
- Andere Nutzer (Programm/.exe, Python-Variante mit `start.*`, Docker, neue Proxmox-Installationen) erhalten nur
  veröffentlichte Releases: Tag `vX.Y.Z` muss zu `app/VERSION` passen. Releases nur auf ausdrücklichen Wunsch des
  Benutzers: annotierter Tag `vX.Y.Z` (Nachricht = Release-Notizen) pushen oder Release auf GitHub veröffentlichen;
  `.github/workflows/release.yml` legt das Release an und baut Docker-Image, .exe und Mac-App.
- Versionsnummer `Major.Minor.Patch` (Semantic Versioning), ausgehend von der letzten veröffentlichten Version:
  **Patch** (1.2.0 → 1.2.1) für Fehlerbehebungen, Sicherheits- und kleine Verbesserungen ohne neue Funktion;
  **Minor** (1.2.x → 1.3.0) sobald eine neue Funktion dabei ist (Patch-Stelle auf 0);
  **Major** (1.x → 2.0.0) nur bei Brüchen, z.B. Rollback mit derselben Datenbank nicht mehr möglich oder manuelle
  Schritte bei der Installation nötig – möglichst vermeiden. Mehrere Änderungen bis zum nächsten Release teilen sich
  eine Nummer (die höchste nötige Stufe), `app/VERSION` und CHANGELOG-Abschnitt werden dann nur angepasst.

## Datenschutz – das Repo soll später öffentlich werden
- Niemals echte Finanzdaten committen: keine eigenen CSV/PDF-Exporte, Datenbank, Sicherungs-JSONs, Kontonummern,
  Beträge oder Screenshots mit echten Werten. `beispiel-csv/` enthält ausschliesslich erfundene Werte.
- Keine Secrets (`.env`, Zertifikate, Schlüssel, Tokens) und keine internen IPs, Hostnamen oder Benutzernamen des Homelabs.
- Hochgeladene Dateien des Benutzers sind private Daten: nur lokal zum Testen verwenden, nie ins Repo.
- Vor jedem Commit `git status` und den Diff prüfen; `.gitignore` nicht aufweichen.

## Technische Leitplanken
- Server: nur Python 3 Standardbibliothek + SQLite (`app/*.py`) – keine pip-Pakete. PyInstaller wird nur in
  GitHub Actions zum Bauen der .exe/App benutzt (`packaging/build_app.py`). Mindestversion Python 3.10.
- Lokaler Betrieb (`app/launcher.py`, `app/local_update.py`, Statusfenster `app/status_window.py` mit tkinter, `FD_LOCAL=1`) und Docker (`FD_DOCKER=1`) bei
  Änderungen an Start, Updates oder `/api/health` mitdenken.
- Frontend: Vanilla JS/CSS ohne Build-Schritt und ohne externe Ressourcen (CSP: `script-src 'self'`, keine Inline-Skripte).
- Neue UI-Texte immer zweisprachig: statische Texte in `EN` bzw. `EN_HTML` (`app/static/i18n.js`), dynamische mit `L("de", "en")`,
  Server-Fehlermeldungen in `EN_ERR`.
- Datenbank-Änderungen nur additiv und abwärtskompatibel in `init_db()` (neue Tabellen/Spalten mit Default), damit der
  Rollback auf die vorherige Version mit derselben Datenbank weiter funktioniert.
- Daten liegen ausserhalb des Repos (`/var/lib/finance-dashboard`, `/etc/finance-dashboard.env`). `install.sh` muss
  idempotent bleiben und darf Daten, Passwort, Einstellungen und Zertifikat nie überschreiben.
- Sicherheit beibehalten: Dienst läuft als Benutzer `finance` mit systemd-Härtung; Login-Pflicht für alle `/api/`-Endpunkte;
  POST nur als JSON von gleicher Herkunft. Das Dashboard bekommt keine root-Rechte – privilegierte Aufgaben laufen über
  separate root-Dienste mit eng begrenzten Aufträgen (Muster: `finance-dashboard-update`).
- Externe Abfragen nur serverseitig (Yahoo Finance für Kurse, Google News für den Ticker, offene Daten des Statistischen
  Amts ZH für Gemeindesteuerfüsse), nie direkt aus dem Browser.

## Testen vor dem Push
- `cd app && FD_DATA_DIR=/tmp/fd-test FD_PORT=18080 python3 server.py`, Ersteinrichtung per `/api/setup`,
  Importe mit den Dateien aus `beispiel-csv/` prüfen.
- `python3 -m py_compile app/*.py finance-dashboard-update tests/*.py packaging/*.py`, `for f in app/static/*.js; do node --check "$f"; done`, `bash -n install.sh`.
- `python3 tests/calc_unit.py`, `python3 tests/update_verify.py`, `python3 tests/smoke_server.py`, `python3 tests/smoke_local.py` und `python3 tests/local_wipe.py` (laufen auch in GitHub Actions auf Windows/macOS/Linux).
- Bei UI-Änderungen einen Screenshot (hell/dunkel, Desktop/Handy) ansehen; README bei neuen Funktionen ergänzen, und zwar in beiden Sprachen: `README.md` (Englisch, Standardansicht auf GitHub) und `README.de.md` (Deutsch) parallel pflegen.

## Arbeitsteilung (Projektleiter und Arbeiter)
- Die Hauptsitzung ist der Projektleiter: Sie plant, zerlegt Aufgaben, delegiert, prüft die Ergebnisse und ist allein für Commit und Push zuständig.
- Arbeiter sind Subagenten (Modell laut `model:` im Frontmatter), definiert in `.claude/agents/`: `umsetzer` (Code schreiben) und `tester` (Tests nach dem Abschnitt «Testen vor dem Push»).
- Kleine Änderungen macht der Projektleiter direkt; Subagenten lohnen sich erst bei grösseren Umbauten oder mehreren unabhängigen Teilaufgaben.
- Vor jedem Commit prüft der Projektleiter den Diff selbst (Datenschutz-Abschnitt), auch wenn der `tester` gemeldet hat, alles sei in Ordnung.
