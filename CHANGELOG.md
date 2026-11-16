# Änderungen

Neue Versionen oben. Den Abschnitt einer Version beim Veröffentlichen auf GitHub als Release-Notizen einfügen
(siehe README → «Neue Version veröffentlichen»).

## 1.0.1

### Behoben
- Windows: «GitHub nicht erreichbar: CERTIFICATE_VERIFY_FAILED» bei der Update-Prüfung. Fehlt im
  Windows-Zertifikatsspeicher ein Stammzertifikat, lässt das Dashboard Windows es jetzt automatisch ergänzen und
  versucht es erneut. Das gilt auch für Kurse, News und Steuerfüsse.

**Update:** *Einstellungen → Updates → Jetzt aktualisieren*. Erscheint unter Windows noch die Zertifikatsmeldung,
öffne einmal https://api.github.com in Edge und klicke danach auf *Nach Updates suchen*.

*English:* Fixes the certificate error on Windows when checking for updates.

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
