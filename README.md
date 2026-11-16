# Finanz-Dashboard

Schlankes, selbst gehostetes Portfolio-Dashboard – lokal auf deinem Computer (Windows, macOS, Linux)
oder auf einem eigenen Server bzw. im Proxmox-Homelab.
Importiert CSV-Exporte von **Relai** (Bitcoin) und **Interactive Brokers** (ETFs/Aktien) sowie beliebige andere CSVs per Spalten-Zuordnung.

- Keine externen Python-Pakete – nur Python 3 + SQLite
- Live-Kurse und Wechselkurse über Yahoo Finance (ohne API-Key), automatisch alle 4 h
- Bewertung in Basiswährung (Standard CHF), Kostenbasis nach Durchschnittskosten-Methode
- Duplikat-Erkennung: dieselbe Datei (oder IBKR-Statement + Flex Query) kann mehrfach importiert werden
- Hell/Dunkel-Modus, funktioniert auch auf dem Handy
- Spartipps & Vorsorge: Steuer-Schätzung, 3a-Stand, FIRE-/Pensionsrechner und Fristen (Kanton Zürich)

## Lokal nutzen (ohne Server)

Für Windows, macOS und Linux, ohne Installation und ohne Administratorrechte. Das Dashboard läuft nur auf deinem
Computer (`http://127.0.0.1:8765`) und ist im Netzwerk nicht erreichbar.

1. **Python 3.9 oder neuer** installieren, falls noch nicht vorhanden:
   [python.org/downloads](https://www.python.org/downloads/). Unter Windows dabei *Add python.exe to PATH* anhaken.
   Unter Linux ist Python meist schon installiert.
2. **Neueste Version herunterladen:** Auf GitHub unter *Releases* bei der obersten Version *Source code (zip)*
   herunterladen und an einen beliebigen Ort entpacken, z.B. in *Dokumente*.
3. **Starten** per Doppelklick:
   - Windows: `start.bat`
   - macOS: `start.command`. Beim ersten Mal *Rechtsklick → Öffnen*, weil die Datei aus dem Internet stammt.
   - Linux: `./start.sh` im Terminal

   Es öffnet sich ein kleines Fenster und danach der Browser. Beim ersten Aufruf legst du Benutzername und Passwort fest.

**Beenden:** *Einstellungen → Programm → Dashboard beenden*, oder das Fenster des Startprogramms schliessen
bzw. dort `Strg+C` drücken. Ein erneuter Doppelklick öffnet ein bereits laufendes Dashboard nur im Browser,
statt es doppelt zu starten.

**Deine Daten** liegen getrennt vom Programm im Benutzerprofil. Sie bleiben bei Updates erhalten, und der
Download-Ordner kann jederzeit ersetzt werden:

| System  | Datenordner |
|---------|-------------|
| Windows | `%LOCALAPPDATA%\FinanzDashboard` |
| macOS   | `~/Library/Application Support/FinanzDashboard` |
| Linux   | `~/.local/share/finanz-dashboard` |

Mit der Umgebungsvariable `FD_DATA_DIR` lässt sich ein anderer Ordner wählen, mit `FD_PORT` ein anderer Port.
Zusätzlich empfohlen: ab und zu unter *Einstellungen → Sicherung* eine Sicherung herunterladen.

**Updates:** Unter *Einstellungen → Updates* prüft das Dashboard einmal täglich bei GitHub, ob es ein neues Release
gibt. Installiert wird nur auf Klick: Das Dashboard lädt die neue Version in den Datenordner, startet neu und
prüft, ob sie läuft. Startet sie nicht, kehrt es automatisch zur bisherigen Version zurück. Zusätzlich gibt es den
Button *Vorherige Version wiederherstellen*. Lädst du später von Hand einen neueren Download herunter, wird
automatisch dieser verwendet.

**Passwort vergessen?** Im entpackten Ordner:
`FD_DATA_DIR=<Datenordner> python3 app/server.py --reset-password`. Unter Windows zuerst
`set FD_DATA_DIR=%LOCALAPPDATA%\FinanzDashboard` ausführen, danach `py app\server.py --reset-password`.

## Installation auf Proxmox (LXC)

1. **Container anlegen** (auf dem Proxmox-Host, Debian-12-Template):
   ```bash
   pveam update && pveam download local debian-12-standard_12.7-1_amd64.tar.zst
   pct create 120 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
     --hostname finance --cores 1 --memory 512 --rootfs local-lvm:4 \
     --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --features nesting=1 --start 1
   ```
   (ID, Storage und Template-Version an dein System anpassen – `pveam available | grep debian` zeigt die aktuelle Version.)

2. **Code holen** und installieren (im Container):
   ```bash
   pct enter 120
   apt update && apt install -y git
   git clone https://github.com/Kevegl/finance-dashboard.git /root/finance-dashboard
   cd /root/finance-dashboard && bash install.sh
   ```

3. Im Browser öffnen: `https://<IP-des-Containers>:8080` (Zertifikatswarnung einmalig bestätigen)

### Einstellungen
In `/etc/finance-dashboard.env`, danach `systemctl restart finance-dashboard`:

| Variable | Bedeutung | Standard |
|---|---|---|
| `FD_PORT` | Port | `8080` |
| `FD_BASE_CCY` | Start-Basiswährung (danach in den Einstellungen änderbar) | `CHF` |
| `FD_PRICE_TTL` | Kurs-Aktualisierung in Sekunden | `14400` |

**Sicherheit:** `install.sh` aktiviert automatisch **HTTPS** (selbstsigniertes Zertifikat). Beim **ersten Aufruf** legst du im Browser Benutzername und Passwort fest (gespeichert als PBKDF2-Hash, Anmeldung per sicherem Sitzungs-Cookie, 30 Tage gültig). Passwort ändern, andere Geräte abmelden und CSV-Export findest du unter **Einstellungen** (Zahnrad oben rechts). Nach 5 falschen Passwörtern wird die IP 15 Minuten gesperrt.
Passwort vergessen: im Container `cd /opt/finance-dashboard/app && FD_DATA_DIR=/var/lib/finance-dashboard python3 server.py --reset-password` – danach erscheint wieder die Ersteinrichtung.
Von aussen nur über VPN (WireGuard/Tailscale) erreichbar machen, keine Portweiterleitung im Router.

**Sicherung:** Unter *Einstellungen → Sicherung & Wiederherstellung* lädst du eine JSON-Datei mit allen Portfolios, Transaktionen, Ticker-Einstellungen und der Kurshistorie herunter (ohne Passwort). Nach einer Neuinstallation: Zugang einrichten → Sicherung wiederherstellen. Zusätzlich empfohlen: Container 120 im Proxmox-Backup-Job.

**Mehrere Portfolios:** Über die Tabs oben im Dashboard (+ = neues Portfolio, Doppelklick = umbenennen). Jedes Portfolio hat eigene Transaktionen und Importe; verwalten/löschen unter *Einstellungen → Portfolios*.

**Update:** am einfachsten im Dashboard unter *Einstellungen → Updates* (siehe unten). Alternativ im Container: `cd /root/finance-dashboard && git pull && bash install.sh` (Daten und Einstellungen bleiben erhalten – sie liegen ausserhalb des Repos in `/var/lib/finance-dashboard` und `/etc/finance-dashboard.env`).

## Updates im Dashboard

Wurde das Dashboard per `git clone` installiert, richtet `install.sh` zusätzlich einen kleinen Update-Dienst ein.
Unter *Einstellungen → Updates* siehst du dann die installierte Version, ob es eine neue gibt (mit Änderungsliste)
und kannst sie per Klick installieren. Ein Punkt am Zahnrad zeigt an, dass ein Update bereitliegt.

- **Tägliche Prüfung:** Ein systemd-Timer prüft einmal am Tag, ob es eine neue Version gibt. Installiert wird nur auf Klick.
- **Kanal** (`FD_UPDATE_CHANNEL` in `/etc/finance-dashboard.env`): Mit `release` gibt es nur veröffentlichte
  Versionen (Git-Tags `v1.2.3`). Das ist der Standard bei Neuinstallationen. Mit `main` ist jeder neue Commit ein
  Update. Ohne Eintrag gilt `main`, so verhalten sich bestehende Installationen wie bisher.
- **Sicherheit:** Das Dashboard läuft als eingeschränkter Benutzer und schreibt beim Klick nur eine Auftragsdatei.
  Ausgeführt wird sie vom root-Dienst `finance-dashboard-update`, der ausschliesslich `check`, `update` und `rollback`
  akzeptiert, nur Fast-Forward-Updates aus dem eigenen Repository holt und lokale Änderungen nie überschreibt.
- **Automatischer Rollback:** Antwortet das Dashboard nach dem Update nicht, wird die vorherige Version wiederhergestellt.
  Zusätzlich gibt es den Button *Vorherige Version wiederherstellen*.
- Im Container von Hand: `finance-dashboard-update check | update | rollback`, Status in
  `/var/lib/finance-dashboard-update/status.json`, Timer: `systemctl list-timers finance-dashboard-update-check.timer`.

**Privates Repository:** Der Container braucht einen Lesezugriff, am besten einen Deploy Key:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/finance_deploy -N "" -C "finance-lxc"
printf 'Host github.com\n  IdentityFile ~/.ssh/finance_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
cat ~/.ssh/finance_deploy.pub   # auf GitHub: Repo → Settings → Deploy keys → Add (ohne Schreibzugriff)
git clone git@github.com:Kevegl/finance-dashboard.git /root/finance-dashboard
```

## CSV exportieren

**Relai:** App → *Transaktionen* → Pfeil oben rechts → Zeitraum wählen → Format *CSV*.
Käufe und Verkäufe werden übernommen; Auszahlungen in die eigene Wallet werden übersprungen (der BTC gehört dir ja weiterhin).

**Interactive Brokers** – zwei Varianten, beide werden automatisch erkannt:
- *Activity Statement*: Client Portal → Performance & Berichte → Kontoauszüge → *Aktivität* → Zeitraum wählen → Format **CSV**. Enthält auch Dividenden und die Börse (für automatische Kurs-Ticker).
- *Flex Query*: Performance & Berichte → Flex Queries → Aktivität-Flex-Query mit Sektion *Trades* (Felder u.a. Symbol, TradeDate, Quantity, TradePrice, CurrencyPrimary, IBCommission, Buy/Sell, ListingExchange) → Format CSV.

Tipp: Bei IBKR das Statement für ein ganzes Jahr bzw. seit Kontoeröffnung exportieren. Devisentausch-Trades (z.B. CHF→USD) werden übersprungen.

**Andere Anbieter** (Swissquote, PostFinance, Yuh, …): Im Import-Dialog *Anderer Anbieter* wählen und die Spalten (Datum, Asset, Menge, Kurs, Betrag …) zuordnen.

## Kurse & Ticker
Beim Import wird der Yahoo-Ticker automatisch gesetzt, wenn möglich (BTC → `BTC-CHF`, IBKR mit Börse LSE → `VWRA.L`, SIX → `CHSPI.SW` …).
Fehlt ein Ticker, wird mit dem letzten Kaufkurs bewertet – ein Hinweis erscheint oben. Klick auf die Position, um den Ticker zu setzen oder einen manuellen Kurs einzutragen.
Endungen: `.SW` SIX, `.L` London, `.DE` Xetra, `.AS` Amsterdam, `.PA` Paris, ohne Endung = USA.

Der Container braucht dafür Internetzugang zu `query1.finance.yahoo.com`.

## Säule 3a: Finpension und VIAC

- **Finpension:** Transaktionsreport als CSV exportieren und importieren (wird automatisch erkannt).
- **VIAC:** bietet keinen CSV-Export – stattdessen die PDF-Belege importieren (mehrere PDFs auf einmal oder als ZIP).
  Erkannt werden Börsenabrechnungen (Kauf/Verkauf), Ausschüttungen, Zinsen und Verwaltungsgebühren.
- Ein-/Auszahlungen und Transfers zwischen Stiftungen sind reine Geldbewegungen und werden übersprungen.
- Zinsen und Kontogebühren laufen über die Position „CASH“ und fliessen in „Erträge netto“ ein.
- Die Fonds haben meist keinen Yahoo-Kurs – bewertet wird mit dem letzten Kauf-/Verkaufskurs (bei monatlichen
  Käufen recht aktuell) oder einem manuellen Kurs (Position anklicken).
- Der PDF-Import braucht `pdftotext` (Paket `poppler-utils`, wird von install.sh installiert).

## Spartipps & Vorsorge

Über das Glühbirnen-Symbol oben (oder `#/vorsorge`) öffnet sich eine eigene Ansicht:

- **Als Nächstes:** kommende Fristen nach Datum (Krankenkasse 30.11., 3a/PK-Einkauf bis Jahresende,
  Steuererklärung …) und wie viel du dieses Jahr noch in die Säule 3a einzahlen kannst – gezählt werden die
  Einzahlungen in deinen 3a-Portfolios.
- **Steuern sparen:** Schätzung von Einkommenssteuer, Grenzsteuersatz und Ersparnis durch 3a-Einzahlung,
  3a-Nachzahlung und PK-Einkauf (einmalig vs. gestaffelt). Den Grenzsteuersatz aus der eigenen Steuerrechnung kannst du
  direkt eintragen.
- **Gemeindesteuerfüsse:** Für den Kanton Zürich holt der Server die aktuellen Steuerfüsse automatisch (höchstens
  einmal täglich) aus den offenen Daten des Statistischen Amts (`www.web.statistik.zh.ch`, Datensatz «Steuerfüsse der
  Zürcher Gemeinden» auf opendata.swiss). Neue Jahre erscheinen, sobald der Kanton sie publiziert; im Profil lässt sich
  der Abruf auslösen oder abschalten. Alternativ die CSV manuell hochladen (das Jahr wird aus dem Dateinamen gelesen,
  hochgeladene Tabellen werden vom Abruf nicht überschrieben). Danach Wohngemeinde und
  Konfession wählen; Gemeinden mit mehreren Steuerfüssen (Schul-/Kirchgemeinden) bieten eine Variantenauswahl.
  Zusätzlich gibt es einen Vergleich mit den günstigsten und teuersten Gemeinden.
- **Mehrere 3a-Konten:** z.B. eigenes und Partner-Konto einzeln anzeigen oder vergleichen; pro Konto lassen sich
  PK-Anschluss, Erwerbseinkommen und steuerbares Einkommen festlegen.
- **FIRE & Pensionierung:** FIRE-Zahl, frühestes Ausstiegsalter, Brücke bis 65 inkl. AHV-Beiträgen als
  Nichterwerbstätige und Rentenlücke. Das Vermögen wird aus den Portfolios übernommen (überschreibbar).
- **Tipps & Tricks:** rund 45 Tipps zu Steuern, Vorsorge, Anlegen, Alltag, FIRE, Pensionierung und anstehenden
  Gesetzesänderungen – mit persönlichen Zahlen aus dem Profil und zum Abhaken.

**Jahreswechsel:** 3a-Einzahlungen und Fristen beziehen sich immer auf das laufende Jahr. Jährliche Tipps
(3a, Krankenkasse, Abzüge …) sind am 1. Januar wieder offen, einmalige bleiben erledigt; befristete Tipps verschwinden
nach ihrem Gültigkeitsjahr. Amtliche Jahreswerte (AHV-Maximalrente, 3a-Maximum, Bundestarif) stehen in
`YEAR_VALUES`/`FED_BY_YEAR` in `app/static/plan.js` und kommen per Update. Fehlen sie für ein neues Jahr, gelten die
Werte des Vorjahres mit einem Hinweis – dort lässt sich die neue AHV-Maximalrente eintragen, die 3a-Maximalbeträge
(24 % bzw. 120 % der jährlichen Maximalrente) werden daraus berechnet.

Das Profil wird auf dem Server gespeichert (in der Datenbank, also auch in der Sicherung). Alle Tarife sind
Näherungen (Bundessteuer nach amtlichem Tarif 2026, Staatssteuer vereinfacht) und ersetzen keine Beratung. Weitere Kantone lassen sich in `app/static/plan.js`
(`CANTONS`, `DEADLINES`, `TIPS` mit `canton`) ergänzen.

## Sprache & News-Ticker

- **Sprache:** Deutsch oder Englisch unter *Einstellungen → Allgemein*. Gilt für das ganze Dashboard und die News.
- **News-Ticker:** Laufschrift über den Portfolio-Tabs mit aktuellen Schlagzeilen (Google News, letzte 14 Tage) zu den
  gehaltenen Positionen des geöffneten Portfolios. Der Server holt die Meldungen und speichert sie 30 Minuten zwischen;
  der Browser verbindet sich nicht direkt mit Google. Abschaltbar in den Einstellungen.
- Suchbegriff pro Position anpassbar: Position anklicken → *News-Suchbegriff* (leer = Name der Position).
- **Säule 3a:** Vorsorgefonds stehen nie in Schlagzeilen. In 3a-Portfolios ordnet der Ticker die Fonds deshalb ihrem
  Markt zu (z. B. SMI, Weltbörsen, Wall Street, Schwellenländer, Immobilien Schweiz, SNB-Zinsen, Gold) und zeigt
  zusätzlich Meldungen zum Anbieter (VIAC, Finpension) und zur Säule 3a allgemein. Ein eigener News-Suchbegriff
  einer Position hat Vorrang.

## Lokal testen
```bash
cd app && python3 server.py      # http://localhost:8080
```
Im Ordner `beispiel-csv/` liegen Testdateien für alle Formate (erfundene Werte).

## Datenschutz
Das Repository enthält nur Code und erfundene Beispieldaten. Datenbank, Sicherungen, eigene CSV/PDF-Exporte,
`.env`-Dateien und Zertifikate sind per `.gitignore` ausgeschlossen. Lokal getestet landet die Datenbank in `app/data/`
(ebenfalls ignoriert).
