# Änderungen

Neue Versionen oben. Den Abschnitt einer Version beim Veröffentlichen auf GitHub als Release-Notizen einfügen
(siehe README → «Neue Version veröffentlichen»).

## 1.0.3

### Neu
- **Zwei-Faktor-Anmeldung per QR-Code einrichten:** QR-Code mit der Authenticator-App scannen, statt den Schlüssel
  abzutippen (eigener Generator, ohne Fremdbibliothek). Schlüssel zum Eintippen und Handy-Link bleiben.

### Behoben
- Anmeldung mit einem Benutzernamen mit Umlauten (z.B. «Jürg») schlug mit einer Fehlermeldung fehl.
- Programm und Python-Variante: Nach einem erfolgreichen Update erschien kurz «Der letzte Lauf wurde unterbrochen»,
  weil die neue Version schon antwortete, bevor das Ergebnis eingetragen war. Jetzt erscheint «Update installiert».
- Bei 1.0.2 fehlte die Mac-App, weil GitHub keinen Mac-Rechner zum Bauen bereitstellte. Mit 1.0.3 wird sie wieder
  angeboten.

**Update:** *Einstellungen → Updates → Jetzt aktualisieren*. Wer noch 1.0.1 hat, erhält dabei auch alle Neuerungen
von 1.0.2 (Wiederherstellungscode, Zwei-Faktor, Anmeldeprotokoll) – bitte danach unter
*Einstellungen → Wiederherstellungscode* einen Code erzeugen.

*English:* Two-factor setup via QR code; fixes sign-in with non-ASCII usernames and a wrong “interrupted” message
right after a successful update; the macOS app is available again.

## 1.0.2

### Neu
- **Passwort vergessen ohne Befehle:** Bei der Einrichtung erhältst du einmalig einen Wiederherstellungscode. Mit ihm
  setzt du auf der Anmeldeseite unter *Passwort vergessen?* ein neues Passwort. Bestehende Installationen erzeugen
  den Code unter *Einstellungen → Wiederherstellungscode*. Im lokalen Betrieb geht es zusätzlich über
  *Passwort zurücksetzen …* im Statusfenster.
- **Zwei-Faktor-Anmeldung (optional)** mit einer Authenticator-App (6-stelliger Code), einzuschalten unter
  *Einstellungen → Zwei-Faktor-Anmeldung*.
- **Anmeldeprotokoll** unter *Einstellungen → Sitzungen*: letzte Anmeldung, Fehlversuche der letzten 30 Tage und
  die letzten Ereignisse mit Zeit und Adresse.
- **Server (Proxmox/LXC):** automatische Debian-Sicherheitsupdates und eine deutlich strengere Abschottung des
  Dienstes.

### Verbessert
- Auch falsche Passwörter in den Einstellungen zählen zur Sperre nach 5 Fehlversuchen.
- Handy: Kopfzeile mit Titel in einer Zeile und Knöpfen darunter; das Logo wird nicht mehr gequetscht.

**Update:** *Einstellungen → Updates → Jetzt aktualisieren*. Danach unter *Einstellungen → Wiederherstellungscode*
einen Code erzeugen und sicher aufbewahren.

*English:* Password recovery code (no commands needed), optional two-factor sign-in, sign-in log, automatic
security updates and stronger service hardening on servers, improved header on phones.

## 1.0.1

### Neu
- Statusfenster «Finanz-Dashboard – Läuft auf diesem Computer» mit *Im Browser öffnen* und *Beenden*: Man sieht
  jederzeit in der Taskleiste bzw. im Dock, dass das Dashboard noch läuft, auch wenn der Browser geschlossen ist.
  Für das Programm (.exe/.app) und die Python-Variante; Sprache folgt der Einstellung im Dashboard.

### Behoben
- Windows: «GitHub nicht erreichbar: CERTIFICATE_VERIFY_FAILED» bei der Update-Prüfung. Fehlt im
  Windows-Zertifikatsspeicher ein Stammzertifikat, lässt das Dashboard Windows es jetzt automatisch ergänzen und
  versucht es erneut. Das gilt auch für Kurse, News und Steuerfüsse.
- Nach «Dashboard beenden» nennt die Seite das passende Programm zum erneuten Öffnen (`FinanzDashboard.exe`,
  `FinanzDashboard.app` bzw. das Startskript) statt aller drei Startskripte.

**Update:** *Einstellungen → Updates → Jetzt aktualisieren*. Erscheint unter Windows noch die Zertifikatsmeldung,
öffne einmal https://api.github.com in Edge und klicke danach auf *Nach Updates suchen*.

*English:* New status window showing that the dashboard is running (with “Open in browser” and “Quit”);
fixes the certificate error on Windows when checking for updates.

## 1.0.0

Erste öffentliche Version des selbst gehosteten Portfolio-Dashboards für Relai, Interactive Brokers,
Finpension und VIAC – mit Live-Kursen, mehreren Portfolios, Spartipps & Vorsorge (Kanton Zürich)
und zweisprachiger Oberfläche (Deutsch/Englisch).

### Herunterladen

| Du hast … | So geht's |
|---|---|
| Windows | `FinanzDashboard-1.0.0-windows-x64.zip` entpacken, `FinanzDashboard.exe` starten |
| Mac mit Apple-Chip (M1 oder neuer) | `FinanzDashboard-1.0.0-macos-arm64.zip` entpacken, `FinanzDashboard.app` mit *Rechtsklick → Öffnen* starten |
| Python 3.10+ (Windows, Mac, Linux) | *Source code (zip)* entpacken, `start.bat` / `start.command` / `start.sh` starten |
| NAS, Raspberry Pi, Docker | [`docker-compose.yml`](https://github.com/Kevegl/finance-dashboard/blob/main/docker-compose.yml), Image `ghcr.io/kevegl/finance-dashboard:1.0.0` |
| Proxmox / Linux-Server | Anleitung im [README](https://github.com/Kevegl/finance-dashboard#installation-auf-proxmox-lxc) |

Beim ersten Start warnen Windows bzw. macOS, weil das Programm nicht kostenpflichtig signiert ist (siehe README).
Deine Daten bleiben auf deinem Gerät. Spätere Updates installierst du per Klick unter *Einstellungen → Updates*.

### Neu
- Lokaler Betrieb ohne Server: Start per Doppelklick, Daten im Benutzerprofil, «Dashboard beenden»
- Eigenständiges Programm für Windows und macOS, ohne Python-Installation
- Selbst-Update über GitHub Releases mit Startprüfung und automatischer Rückkehr zur vorherigen Version
- Docker-Image für amd64 und arm64 inkl. PDF-Import
- Update-Kanal «Releases» für neue Proxmox-Installationen

*English:* First public release. Download the Windows or macOS program above, or use the Python variant
(Source code + start script), Docker or the Proxmox installer – see the README for details.
