# Arbeitsanweisungen für Claude – Finanz-Dashboard

Selbst gehostetes Portfolio-Dashboard (Relai, IBKR, Finpension, VIAC) für ein Proxmox-Homelab.
Sprache im Projekt: Deutsch (Schweizer Schreibweise, „ss“ statt „ß“), UI zweisprachig DE/EN.

## GitHub ist die einzige Quelle
- Repository: `Kevegl/finance-dashboard`, Branch `main`. Keine Zips mehr erstellen oder verteilen.
- Zu Beginn jeder Sitzung das Repo hinzufügen (`add_repo`, Zugriff `push`) und klonen; immer auf dem aktuellen `main` arbeiten.
- Jede Änderung als eigener, nachvollziehbarer Commit mit deutscher Commit-Nachricht
  (Zusammenfassung + Stichpunkte), Autor `Kevegl <Kevegl@users.noreply.github.com>`, danach pushen.
- Nicht direkt auf `main` force-pushen und keine Historie umschreiben – das Update im Container akzeptiert nur Fast-Forwards.

## Auslieferung
- Der Container `finance` (Debian-LXC) ist per `git clone` installiert (`/root/finance-dashboard`, Deploy Key, nur lesend).
- Updates installiert der Benutzer im Dashboard unter *Einstellungen → Updates* (Dienst `finance-dashboard-update`).
  Fallback im Container: `cd /root/finance-dashboard && git pull && bash install.sh`.
- Nach einem Push dem Benutzer kurz sagen, was sich ändert und dass er es über *Updates* installieren kann.

## Datenschutz – das Repo soll später öffentlich werden
- Niemals echte Finanzdaten committen: keine eigenen CSV/PDF-Exporte, Datenbank, Sicherungs-JSONs, Kontonummern,
  Beträge oder Screenshots mit echten Werten. `beispiel-csv/` enthält ausschliesslich erfundene Werte.
- Keine Secrets (`.env`, Zertifikate, Schlüssel, Tokens) und keine internen IPs, Hostnamen oder Benutzernamen des Homelabs.
- Hochgeladene Dateien des Benutzers sind private Daten: nur lokal zum Testen verwenden, nie ins Repo.
- Vor jedem Commit `git status` und den Diff prüfen; `.gitignore` nicht aufweichen.

## Technische Leitplanken
- Server: nur Python 3 Standardbibliothek + SQLite (`app/server.py`, `app/importers.py`) – keine pip-Pakete.
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
- Externe Abfragen nur serverseitig (Yahoo Finance für Kurse, Google News für den Ticker), nie direkt aus dem Browser.

## Testen vor dem Push
- `cd app && FD_DATA_DIR=/tmp/fd-test FD_PORT=18080 python3 server.py`, Ersteinrichtung per `/api/setup`,
  Importe mit den Dateien aus `beispiel-csv/` prüfen.
- `python3 -m py_compile app/*.py finance-dashboard-update`, `node --check app/static/*.js`, `bash -n install.sh`.
- Bei UI-Änderungen einen Screenshot (hell/dunkel, Desktop/Handy) ansehen; README bei neuen Funktionen ergänzen.

## Arbeitsteilung (Projektleiter und Arbeiter)
- Die Hauptsitzung (Opus) ist der Projektleiter: Sie plant, zerlegt Aufgaben, delegiert, prüft die Ergebnisse und ist allein für Commit und Push zuständig.
- Arbeiter sind Subagenten mit Sonnet 5.5, definiert in `.claude/agents/`: `umsetzer` (Code schreiben) und `tester` (Tests nach dem Abschnitt «Testen vor dem Push»).
- Kleine Änderungen macht der Projektleiter direkt; Subagenten lohnen sich erst bei grösseren Umbauten oder mehreren unabhängigen Teilaufgaben.
- Vor jedem Commit prüft der Projektleiter den Diff selbst (Datenschutz-Abschnitt), auch wenn der `tester` gemeldet hat, alles sei in Ordnung.
