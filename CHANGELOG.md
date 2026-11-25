# Änderungen

Neue Versionen oben. Den Abschnitt einer Version beim Veröffentlichen auf GitHub als Release-Notizen einfügen
(siehe README → «Neue Version veröffentlichen»).

## 1.3.2

### Verbessert
- **Animationen im Dashboard:** Beim Öffnen des Dashboards (und beim Wechsel des Portfolios) zeichnet sich die
  *Wertentwicklung* von links nach rechts. Gewinn/Verlust zählt hoch; positive Renditen steigen von unten, negative
  fallen von oben ein (auch in den Spalten *24h* und *G/V* der Positionen). Der Unterstrich der Portfolio-Tabs gleitet
  zum gewählten Portfolio. Bei «Bewegung reduzieren» im Betriebssystem werden die Werte nur sanft eingeblendet.

*English:* Improved: the dashboard animates on opening – the performance chart draws from left to right, gains/losses
count up and slide in by direction, and the portfolio tab underline slides to the selected tab. With “reduce motion”
enabled, values only fade in.

## 1.3.1

### Behoben
- **Spalte 24h zeigte bei Aktien und ETFs vor Handelsbeginn 0 %:** Der Live-Kurs von Yahoo wurde mit dem heutigen
  Datum gespeichert, obwohl die Börse (z.B. in den USA bis 15:30 Uhr Schweizer Zeit) noch geschlossen war. Er gilt
  jetzt für den Handelstag, zu dem er gehört. Vor Handelsbeginn und am Wochenende zeigt die Spalte also die
  Veränderung des letzten Handelstags, statt 0 %. Falsch gespeicherte Kurse werden bei der nächsten
  Kursaktualisierung ersetzt. Kleinste Rundungsdifferenzen erscheinen nicht mehr als «▼ −0 %».

*English:* Fixed: the *24h* column showed 0 % for stocks and ETFs before the market opened (and at weekends); it now
shows the change on the last trading day. Prices stored with the wrong date are replaced on the next price refresh.

## 1.3.0

### Neu
- **Mehrere Benutzer:** Auf der Anmeldeseite gibt es *Neues Konto erstellen*. Jeder Benutzer erhält ein eigenes
  Dashboard mit eigenen Portfolios, Transaktionen, Einstellungen und eigener Anmeldung (Passwort, Zwei-Faktor,
  Wiederherstellungscode) und sieht nur seine Daten. Technisch hat jeder weitere Benutzer eine eigene Datenbank
  (`users/u<Nummer>.db`); die bisherige `finance.db` bleibt unverändert die des Administrators.
- **Benutzerverwaltung:** Der Administrator (Benutzer aus der Ersteinrichtung) sieht unter *Einstellungen → Benutzer*
  alle Benutzer, kann sie löschen und die Registrierung ausschalten. Updates installiert nur der Administrator.
- *Passwort vergessen?* fragt zusätzlich nach dem Benutzernamen.

*English:* Several users can now share one installation: *Create a new account* on the sign-in page gives each user
their own dashboard (own database, portfolios, settings and sign-in). The administrator manages users and sign-up under
*Settings → Users*.

## 1.2.1

### Verbessert
- **Updates zeigen die Versionsnummer:** Unter *Einstellungen → Updates* steht bei der installierten Version jetzt
  «Version 1.2.0» statt nur der Commit-Nachricht, und der Status lautet z.B. «Version 1.2.1 verfügbar · 3 Änderungen»
  statt «3 neue Versionen verfügbar». Änderungen ohne neue Versionsnummer (z.B. nur Dokumentation) erscheinen als
  «neue Änderungen». Die Liste der einzelnen Änderungen bleibt darunter sichtbar.

*English:* *Settings → Updates* now shows the version number (e.g. “Version 1.2.1 available · 3 changes”) instead of
counting every change as a new version.

## 1.2.0

### Neu
- **Kursveränderung der letzten 24 Stunden:** Die Positionstabelle hat eine neue Spalte *24h*. Sie zeigt für jede
  Position mit Live-Kurs die Veränderung gegenüber dem Vortag in Prozent (z.B. ▼ −1.25 %), grün bei Gewinn, rot bei
  Verlust, gerechnet in der Kurswährung. Bei manuellen Kursen, Positionen ohne Ticker, geschlossenen Positionen und
  veralteten Kursen (älter als 3 Tage) bleibt das Feld leer.

*English:* The holdings table has a new *24h* column showing each position's price change versus the previous day in
percent (in the quote currency); empty for manual prices, positions without a ticker, closed positions and stale prices.

## 1.1.5

### Verbessert
- *Neu beginnen – alle Daten löschen* im Statusfenster bestätigst du jetzt mit `ENTFERNEN`, ohne Umlaut, also auf
  jeder Tastatur leicht zu tippen. `LÖSCHEN`, `LOESCHEN` und `DELETE` werden ebenfalls angenommen, Gross- und
  Kleinschreibung spielt keine Rolle.

*English:* “Start over” in the status window is now confirmed by typing `DELETE` (German: `ENTFERNEN`, no umlaut).

## 1.1.4

### Behoben
- **«Neu beginnen» im Statusfenster (lokaler Betrieb):** Wurde während eines Updates oder Neustarts bestätigt, konnte
  die Überwachung des Dashboards abbrechen oder die Daten erst später, beim nächsten Neustart, gelöscht werden. Jetzt
  ist «Neu beginnen» nur möglich, solange das Dashboard normal läuft. Dabei wird auch das Serverprotokoll gelöscht.
- **Statusfenster unter Linux:** Der Dialog «Passwort vergessen …» ist jetzt zuverlässig modal (bisher Fehler
  «grab failed», und der Dialog liess sich mehrfach öffnen).
- **ZIP-Import:** Doppelte Dateien im Archiv zählen nicht mehr zur Grenze von 500 MB.

*English:* «Start over» in the status window is only possible while the dashboard is running normally (previously a
click during an update or restart could stop the supervision or delete the data later) and also deletes the server
log; the «Forgot password …» dialog is reliably modal on Linux; duplicate files in a ZIP no longer count toward the
500 MB limit.

## 1.1.3

### Geändert (Sicherheit)
- **Lokaler Betrieb (Programm/Python): Schutz vor DNS-Rebinding.** Das Dashboard beantwortet nur noch Anfragen an
  `127.0.0.1` bzw. `localhost`. Bisher hätte eine bösartige Webseite in der kurzen Zeit vor der Ersteinrichtung (die
  lokal keinen Einrichtungscode braucht) über einen auf 127.0.0.1 zeigenden Domainnamen das Passwort festlegen können.
- **Anfragen ohne Anmeldung** (Anmeldung, Einrichtung, Wiederherstellung) dürfen höchstens 64 KB gross sein, und eine
  negative Längenangabe wird abgewiesen. Bisher hätte jemand im Netzwerk damit den Speicher des Servers füllen können.
- **ZIP-Import:** Entpackt höchstens 500 MB insgesamt (Schutz vor ZIP-Bomben).
- **Statische Dateien:** strengere Pfadprüfung (nur innerhalb von `app/static`).
- **Anzeige:** Typ und Währung von Transaktionen werden immer maskiert, auch wenn sie aus einer manipulierten
  Sicherungsdatei stammen.
- **Proxmox-Installation:** Der Programmcode unter `/opt/finance-dashboard` gehört jetzt root und ist für den Dienst
  nur lesbar. So kann auch ein übernommenes Dashboard keinen Code ablegen, den root später ausführt.

*English:* The local dashboard now only answers requests for `127.0.0.1`/`localhost` (DNS rebinding protection);
unauthenticated requests are limited to 64 KB; ZIP imports are capped at 500 MB unpacked; stricter static path check;
transaction type/currency always escaped; on Proxmox the program code is owned by root.

## 1.1.2

### Geändert (Sicherheit)
- **Passwort vergessen im lokalen Betrieb (Programm/Python):** Das Statusfenster setzte die Anmeldung bisher ohne
  Nachweis zurück. Wer an einem entsperrten Computer sass, konnte so das Dashboard mit eigenem Passwort öffnen.
  Jetzt bietet *Passwort vergessen …* im Statusfenster nur noch zwei Wege:
  - *Mit Wiederherstellungscode …*: öffnet im Browser das Formular, ein neues Passwort gibt es nur mit dem Code.
  - *Neu beginnen – alle Daten löschen …*: ohne Code. Nach Bestätigung durch Eintippen von `LÖSCHEN` werden alle
    Portfolios, Transaktionen und Einstellungen endgültig gelöscht, danach startet ein leeres Dashboard.

**Tipp:** Unter *Einstellungen → Wiederherstellungscode* prüfen, ob ein Code vorhanden ist, und ihn sicher
aufbewahren. Wirksamen Schutz bei Zugang zum Computer bieten Bildschirmsperre, Computer-Passwort und
Laufwerksverschlüsselung (BitLocker/FileVault).

*English:* “Forgot password” in the status window now requires the recovery code; without it, the only option is to
start over, which deletes all data.

## 1.1.1

### Behoben
- **Windows: «GitHub nicht erreichbar: Das Zertifikat konnte nicht geprüft werden»**, vor allem auf frisch
  eingerichteten PCs. Python kennt nur die Stammzertifikate, die beim Start im Windows-Speicher liegen. Windows lädt
  fehlende aber erst bei Bedarf nach. Das Dashboard überlässt die Prüfung in diesem Fall jetzt Windows selbst, wie
  Edge oder Chrome: Windows prüft Kette, Servername und Gültigkeit und lädt fehlende Zertifikate dabei selbst nach.
  Kein Öffnen von Edge, kein Neustart und kein PowerShell-Umweg mehr nötig. Ungültige Zertifikate werden weiterhin
  abgelehnt. Gilt auch für Kurse, News und Steuerfüsse.

**Update unter Windows, falls die Zertifikatsmeldung gerade erscheint:** das Programm von hier herunterladen und den
Programmordner ersetzen (die Daten bleiben erhalten). Ab 1.1.1 laufen Updates wieder direkt im Dashboard.

*English:* Windows: certificate errors (“GitHub not reachable”) are fixed for good – the check is handed to Windows
itself, which fetches missing root certificates automatically.

## 1.1.0

### Neu
- **Einrichtungscode für die Ersteinrichtung** (Server und Docker): Solange noch kein Passwort festgelegt ist,
  verlangt das Dashboard einen einmaligen Code. So kann niemand im Netzwerk eine frische Installation vor dir
  übernehmen. Der Code steht am Ende der Ausgabe von `install.sh`, im Protokoll (`journalctl -u finance-dashboard`
  bzw. `docker logs finance-dashboard`) und in `setup-code.txt` im Datenordner. Bestehende Installationen und der
  lokale Betrieb (Programm/`start.*`) sind nicht betroffen.
- **Signierte Updates (optional, Proxmox):** Mit der Datei `/etc/finance-dashboard/allowed_signers` installiert der
  Update-Dienst nur Versionen, die mit deinem eigenen SSH-Schlüssel signiert sind. Ein fremder Push auf GitHub
  kann so keinen Code mehr als root im Container ausführen. *Einstellungen → Updates* zeigt das Ergebnis an.
  Anleitung im README unter «Signierte Updates».

### Verbessert
- Interner Umbau: Der Server ist in fachliche Module aufgeteilt (Anmeldung, Datenbank, Kurse, News, Berechnung …),
  Endpunkte laufen über Routing-Tabellen, und Datenbankverbindungen werden nach jeder Abfrage geschlossen.
- Neue automatische Tests für Berechnung, Zahlen- und Datumsformate, Login-Sperre, Einrichtungscode und
  Signaturprüfung.

### Behoben
- **Ungültiges Datum legte das Dashboard lahm:** Eine Transaktion mit einem Datum wie «17.11.2026» oder «2024-13-45»
  (manuell erfasst oder importiert) führte dazu, dass Übersicht und Kursaktualisierung aller Portfolios
  abbrachen. Datum und Zahlen werden jetzt beim Erfassen, Bearbeiten und Importieren geprüft. Bereits gespeicherte
  ungültige Einträge werden übersprungen und in der Übersicht gemeldet.
- **Unendliche Zahlen** (z.B. «1e999» in einer CSV) machten die Antwort des Servers für den Browser unlesbar.
  Sie werden jetzt abgewiesen.
- **Login-Sperre:** Parallele Anfragen konnten die Grenze von 5 Fehlversuchen in 15 Minuten umgehen. Versuche werden
  jetzt atomar gezählt, und es laufen höchstens zwei Passwortprüfungen gleichzeitig.
- **Verkauf ohne erfassten Kauf** (z.B. IBKR-Auszug ab einem späteren Datum): Der Erlös fehlte im realisierten
  Gewinn. Er wird jetzt mitgezählt, mit einem Hinweis in der Übersicht.
- **Sicherung wiederherstellen:** Die Basiswährung der Sicherung gilt sofort, nicht erst nach einem Neustart.
  Fehlerhafte Zeilen in der Sicherungsdatei werden übersprungen.
- Das Datum in der Transaktionsliste wird jetzt immer escapt.

*English:* New one-time setup code protects the first-run setup on servers and Docker; optional signature check
for updates on Proxmox (only releases signed with your own SSH key are installed). Internal restructuring of the
server into modules. Fixes a dashboard outage caused by invalid dates, rejects infinite numbers, makes the login
lockout race-free, counts proceeds of sales without a recorded purchase, and applies the backup's base currency
immediately.

## 1.0.4

### Behoben
- Windows: «GitHub nicht erreichbar: Das Zertifikat konnte nicht geprüft werden» trotz automatischer Ergänzung.
  Windows ergänzte das fehlende Stammzertifikat zwar, das Dashboard las seine Zertifikatsliste aber erst beim
  nächsten Programmstart neu ein. Jetzt geschieht das sofort. Zusätzlich werden die von Windows automatisch
  nachgeladenen Stammzertifikate (Speicher «Drittanbieter-Stammzertifizierungsstellen») direkt berücksichtigt.
  Das betrifft auch Kurse, News und Steuerfüsse.

**Update unter Windows, falls die Zertifikatsmeldung erscheint:** einmal https://api.github.com in Edge öffnen,
das Dashboard über das Statusfenster *Beenden* und neu starten, dann *Einstellungen → Updates → Jetzt
aktualisieren*. Ab 1.0.4 ist das nicht mehr nötig.

*English:* Windows: fixes “certificate could not be verified” right after Windows added a missing root certificate.

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
