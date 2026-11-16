"use strict";
/* Übersetzungen Deutsch -> Englisch.
   - Statische Texte im HTML werden über das Wörterbuch EN (deutscher Text = Schlüssel) ersetzt.
   - Texte mit Formatierung tragen data-i18n-html="schlüssel" und stehen in EN_HTML.
   - Dynamische Texte im JavaScript nutzen L("deutsch", "english"). */

let LANG = "de";
let LOCALE = "de-CH";
const L = (de, en) => (LANG === "en" ? en : de);

const EN = {
  // Kopfzeile
  "Klicken zum Umbenennen": "Click to rename",
  "Kurse aktualisieren": "Refresh prices",
  "Beträge verbergen": "Hide amounts",
  "Beträge anzeigen": "Show amounts",
  "Einstellungen": "Settings",
  "Spartipps & Vorsorge": "Savings tips & retirement",
  "Hell/Dunkel": "Light/Dark",
  "Darstellung wechseln": "Toggle theme",
  "+ Transaktion": "+ Transaction",
  "CSV importieren": "Import CSV",
  "Transaktionen importieren": "Import transactions",
  "Finpension 3a (CSV)": "Finpension pillar 3a (CSV)",
  "Zins": "Interest",
  "Fonds mit ISIN (z.B. 3a-Vorsorgefonds): Der Ticker wird automatisch über die Yahoo-Suche gefunden.":
    "Funds with an ISIN (e.g. pillar 3a funds): the ticker is found automatically via Yahoo search.",
  "Neues Portfolio": "New portfolio",
  "Anlegen": "Create",
  "z.B. Säule 3a": "e.g. pillar 3a",
  "Art des Portfolios": "Portfolio type",
  "Normales Portfolio": "Standard portfolio",
  "Aktien, ETFs, Krypto … Gewinn = Wert minus investiertes Kapital.": "Stocks, ETFs, crypto … Gain = value minus invested capital.",
  "Säule 3a / Vorsorgekonto": "Pillar 3a / pension account",
  "Mit Einzahlungen und Kontoguthaben. Zeigt „Selbst einbezahlt“ und „Gewinn seit Beginn“ – Fondswechsel und Transfers sind neutral.":
    "With deposits and cash balance. Shows “paid in” and “gain since start” – fund switches and transfers are neutral.",
  "Einzahlung": "Deposit",
  "Auszahlung": "Withdrawal",
  // Anmeldung
  "Anmelden": "Sign in",
  "Benutzername": "Username",
  "Passwort": "Password",
  "Passwort wiederholen": "Repeat password",
  // Dashboard
  "Neueste Nachrichten zu deinen Positionen": "Latest news on your holdings",
  "Gesamtwert": "Total value",
  "Investiert": "Invested",
  "Gewinn / Verlust": "Profit / Loss",
  "Erträge netto": "Net income",
  "Wertentwicklung": "Performance",
  "Wert": "Value",
  "1J": "1Y",
  "Aufteilung": "Allocation",
  "Positionen": "Holdings",
  "Klick auf eine Position, um Kurs-Ticker oder Namen anzupassen": "Click a holding to adjust its price ticker, name or news search term",
  "Menge": "Quantity",
  "Ø Kaufkurs": "Avg. cost",
  "Kurs": "Price",
  "G/V": "P/L",
  "Anteil": "Weight",
  "Transaktionen": "Transactions",
  "Suchen…": "Search…",
  "Transaktionen durchsuchen": "Search transactions",
  "Quelle filtern": "Filter by source",
  "Alle Quellen": "All sources",
  "Quelle löschen": "Delete source",
  "Ausgewählte löschen": "Delete selected",
  "Alle Treffer auswählen": "Select all results",
  "Datum": "Date",
  "Typ": "Type",
  "Gebühr": "Fee",
  "Betrag": "Amount",
  "Quelle": "Source",
  "Mehr anzeigen": "Show more",
  "Noch keine Daten": "No data yet",
  "Importiere deinen ersten CSV-Export von Relai, Interactive Brokers oder einem anderen Anbieter.":
    "Import your first CSV export from Relai, Interactive Brokers or another provider.",
  // Einstellungen
  "Allgemein": "General",
  "Titel des Dashboards": "Dashboard title",
  "Basiswährung": "Base currency",
  "Alle Werte, Gewinne und Summen werden in diese Währung umgerechnet.": "All values, gains and totals are converted into this currency.",
  "Sprache": "Language",
  "News-Ticker": "News ticker",
  "News-Ticker über dem Portfolio anzeigen": "Show news ticker above the portfolio",
  "Kursaktualisierung": "Price updates",
  "Speichern": "Save",
  "Portfolios": "Portfolios",
  "Jedes Portfolio ist ein eigenständiges Dashboard mit eigenen Transaktionen. Zwischen den Portfolios wechselst du über die Tabs oben im Dashboard (Doppelklick auf einen Tab = umbenennen).":
    "Each portfolio is a separate dashboard with its own transactions. Switch between portfolios with the tabs at the top of the dashboard (double-click a tab to rename it).",
  "Name des neuen Portfolios, z.B. Säule 3a": "Name of the new portfolio, e.g. Pillar 3a",
  "Name des neuen Portfolios": "Name of the new portfolio",
  "+ Portfolio hinzufügen": "+ Add portfolio",
  "Konto & Passwort": "Account & password",
  "Aktuelles Passwort": "Current password",
  "Neues Passwort": "New password",
  "leer lassen = nicht ändern": "leave empty = keep current",
  "Neues Passwort wiederholen": "Repeat new password",
  "Mindestens 8 Zeichen. Nach einer Passwortänderung werden alle anderen Geräte abgemeldet.":
    "At least 8 characters. Changing the password signs out all other devices.",
  "Änderungen speichern": "Save changes",
  "Sitzungen": "Sessions",
  "Alle anderen Geräte abmelden": "Sign out all other devices",
  "Programm": "Application",
  "Dashboard beenden": "Quit dashboard",
  "Das Dashboard wurde beendet": "The dashboard has been closed",
  "Abmelden": "Sign out",
  "Daten": "Data",
  "Sicherung & Wiederherstellung": "Backup & restore",
  "Sicherung herunterladen": "Download backup",
  "Sicherung wiederherstellen …": "Restore backup …",
  "Export für Excel & Steuererklärung": "Export for Excel & tax return",
  "Transaktionen als CSV exportieren": "Export transactions as CSV",
  // Import
  "Schliessen": "Close",
  "Anbieter": "Provider",
  "Automatisch erkennen": "Detect automatically",
  "Anderer Anbieter – Spalten zuordnen": "Other provider – map columns",
  "CSV-Datei hierher ziehen oder klicken": "Drop a CSV file here or click",
  "Die Datei wird nur auf deinem Server verarbeitet.": "The file is processed on your own server only.",
  "CSV-, PDF- oder ZIP-Dateien hierher ziehen oder klicken": "Drop CSV, PDF or ZIP files here or click",
  "PDF-Belege (z.B. VIAC) können gesammelt oder als ZIP gewählt werden. Alles wird nur auf deinem Server verarbeitet.":
    "PDF statements (e.g. VIAC) can be selected together or as a ZIP. Everything is processed on your own server only.",
  "Abbrechen": "Cancel",
  "Importieren": "Import",
  // Position
  "Position": "Holding",
  "Kurs-Ticker (Yahoo Finance)": "Price ticker (Yahoo Finance)",
  "z.B. BTC-CHF, VWRA.L, CHSPI.SW": "e.g. BTC-CHF, VWRA.L, CHSPI.SW",
  "Börsenkürzel: .SW = SIX, .L = London, .DE = Xetra, .AS = Amsterdam, ohne Endung = USA":
    "Exchange suffix: .SW = SIX, .L = London, .DE = Xetra, .AS = Amsterdam, none = USA",
  "Manueller Kurs (optional, überschreibt Live-Kurs)": "Manual price (optional, overrides live price)",
  "News-Suchbegriff (optional)": "News search term (optional)",
  "Wonach im News-Ticker gesucht wird, z.B. „Bitcoin“ oder „Vanguard Total World“. Leer = Name der Position.":
    "What the news ticker searches for, e.g. “Bitcoin” or “Vanguard Total World”. Empty = holding name.",
  "Kategorie": "Category",
  "ETF / Fonds": "ETF / Fund",
  "Aktie": "Stock",
  "Krypto": "Crypto",
  "Anleihe": "Bond",
  "Andere": "Other",
  // Transaktion
  "Transaktion erfassen": "Add transaction",
  "Kauf": "Buy",
  "Verkauf": "Sell",
  "Dividende": "Dividend",
  "Quellensteuer": "Withholding tax",
  "Betrag total (optional)": "Total amount (optional)",
  "Währung": "Currency",
  "Manuell": "Manual",
  "Notiz": "Note",
  // Bestätigung
  "Bist du sicher?": "Are you sure?",
  "Löschen": "Delete",
  // Updates
  "Installierte Version": "Installed version",
  "Jetzt aktualisieren": "Update now",
  "Nach Updates suchen": "Check for updates",
  "Vorherige Version wiederherstellen": "Restore previous version",
  "Protokoll des letzten Laufs": "Log of the last run",
};

const EN_HTML = {
  updUnavailable: "Updates via the dashboard are only available if it was installed with <code>git clone</code>. See the README for instructions.",
  quitInfo: "You can close this browser window now. Your data is saved. To open the dashboard again, run the start program (<code>start.bat</code>, <code>start.command</code> or <code>start.sh</code>).",
  updHint: "The dashboard checks for a new version automatically every day. Nothing is installed until you click “Update now”. Your data stays unchanged; if the dashboard does not start after the update, the previous version is restored automatically.",
  backupInfo: "The backup contains <b>all portfolios</b>, transactions, ticker settings and the stored price history – but not your password. Keep the file safe, it contains your financial data.",
  restoreInfo: "Restoring <b>replaces all current data</b>. After a reinstall: set up your login, then choose the backup here.",
  exportInfo: "Exports only the transactions of the <b>currently open portfolio</b> as a CSV table (date, type, asset, quantity, price, fee, amount, currency, source). Handy for analysis in Excel, your tax return, or to move a single portfolio into another portfolio/dashboard – the file can be read back via “Import CSV”. For a complete backup use “Download backup”.",
};

/* Fehlermeldungen des Servers */
const EN_ERR = {
  "Ungültige Daten": "Invalid data",
  "Daten zu gross": "Data too large",
  "Keine Steuerfüsse gefunden": "No tax rates found",
  "Unerwartetes Format der Steuerfuss-Daten": "Unexpected format of the tax rate data",
  "Unbekannte Aktion": "Unknown action",
  "Updates über das Dashboard sind bei dieser Installation nicht verfügbar.": "Updates via the dashboard are not available for this installation.",
  "Der letzte Lauf wurde unterbrochen.": "The last run was interrupted.",
  "Es läuft bereits ein Update-Vorgang.": "An update is already running.",
  "Auf GitHub wurden keine Versionen gefunden.": "No versions were found on GitHub.",
  "GitHub hat die Anfrage begrenzt – bitte später erneut versuchen.": "GitHub rate-limited the request – please try again later.",
  "Download zu gross.": "Download too large.",
  "Ungültiger Pfad im Archiv.": "Invalid path in the archive.",
  "Das heruntergeladene Archiv ist beschädigt.": "The downloaded archive is damaged.",
  "Das Archiv enthält kein gültiges Dashboard.": "The archive does not contain a valid dashboard.",
  "Die gespeicherte vorherige Version ist nicht mehr vorhanden.": "The stored previous version no longer exists.",
  "Die neue Version ist unvollständig.": "The new version is incomplete.",
  "Die neue Version startet nicht. Die vorherige Version wurde wiederhergestellt.": "The new version does not start. The previous version was restored.",
  "Für den Kanal «Releases» gibt es noch keine Version (Tag v…) auf GitHub.": "There is no version (tag v…) on GitHub for the “Releases” channel yet.",
  "Es ist keine vorherige Version gespeichert.": "No previous version is stored.",
  "Im Repository gibt es lokale Änderungen – Update abgebrochen (git status im Container).": "The repository has local changes – update cancelled (run git status in the container).",
  "Der lokale Stand weicht von GitHub ab (eigene Commits) – Update abgebrochen.": "The local state differs from GitHub (own commits) – update cancelled.",
  "Keine PDF- oder CSV-Dateien gefunden": "No PDF or CSV files found",
  "PDF-Import nicht verfügbar: pdftotext fehlt (install.sh erneut ausführen)": "PDF import unavailable: pdftotext missing (run install.sh again)",
  "Benutzername oder Passwort falsch.": "Wrong username or password.",
  "Das Dashboard ist bereits eingerichtet.": "The dashboard is already set up.",
  "Das aktuelle Passwort ist falsch.": "The current password is wrong.",
  "Fremde Herkunft": "Request from foreign origin",
  "Nur JSON erlaubt": "Only JSON allowed",
  "Transaktion nicht gefunden": "Transaction not found",
  "Zu viele Fehlversuche – bitte 15 Minuten warten.": "Too many failed attempts – please wait 15 minutes.",
  "nicht gefunden": "not found",
  "Bitte eine Asset-Spalte zuordnen oder ein festes Asset angeben.": "Please map an asset column or enter a fixed asset.",
  "Bitte mindestens Datum und Menge (oder Betrag) zuordnen.": "Please map at least date and quantity (or amount).",
  "Die Datei enthält keine Daten.": "The file contains no data.",
  "IBKR Flex-Query: Spalten Symbol, TradeDate, Quantity, TradePrice werden benötigt.": "IBKR Flex Query: columns Symbol, TradeDate, Quantity, TradePrice are required.",
  "Keine Transaktionen im IBKR-Kontoauszug gefunden.": "No transactions found in the IBKR statement.",
  "Relai-Format nicht erkannt (Spalten 'Date' und 'BTC Amount' fehlen).": "Relai format not recognised (columns 'Date' and 'BTC Amount' missing).",
  "Asset fehlt": "Asset missing",
  "Das ist keine gültige Sicherungsdatei des Finanz-Dashboards.": "This is not a valid backup file of the finance dashboard.",
  "Datei zu gross (max. 100 MB)": "File too large (max. 100 MB)",
  "Die Sicherung enthält keine Portfolios.": "The backup contains no portfolios.",
  "Keine Transaktionen ausgewählt": "No transactions selected",
  "Leere Datei": "Empty file",
  "Name fehlt": "Name missing",
  "Unbekannte Währung": "Unknown currency",
  "Ungültiger Typ": "Invalid type",
  "Ungültiges Datum": "Invalid date",
  "Das Passwort muss mindestens 8 Zeichen lang sein.": "The password must be at least 8 characters long.",
};
const EN_ERR_RX = [
  [/^Steuerfüsse konnten nicht geladen werden: /, "Tax rates could not be loaded: "],
  [/^Zeile (\d+): Datum unlesbar$/, "Row $1: unreadable date"],
  [/^Zeile (\d+): unvollständig$/, "Row $1: incomplete"],
  [/^Zeile (\d+): (.*) übersprungen \(kein Kauf\/Verkauf\)$/, "Row $1: $2 skipped (not a buy/sell)"],
  [/^Zeile (\d+): '(.*)' \(Wallet-Bewegung\) übersprungen$/, "Row $1: '$2' (wallet transfer) skipped"],
  [/^Devisentausch (.*) übersprungen$/, "Currency exchange $1 skipped"],
  [/^Vesting (\S+) (\S+) \(bereits bei Zuteilung gezählt\)$/, "Vesting $1 $2 (already counted at award)"],
  [/^Zeile (\d+): Devisentausch übersprungen$/, "Row $1: currency exchange skipped"],
  [/^Zeile (\d+): Typ '(.*)' übersprungen$/, "Row $1: type '$2' skipped"],
  [/^Zeile (\d+): Menge fehlt$/, "Row $1: quantity missing"],
  [/^Fehler (\d+)$/, "Error $1"],
  [/^GitHub antwortet mit Fehler (\d+)\.$/, "GitHub responded with error $1."],
  [/^GitHub nicht erreichbar: (.*)$/, "GitHub not reachable: $1"],
  [/^Download fehlgeschlagen \(Fehler (\d+)\)\.$/, "Download failed (error $1)."],
  [/^Download fehlgeschlagen: (.*)$/, "Download failed: $1"],
  [/^Zeile (\d+): (.*) \(Ein-\/Auszahlung\) übersprungen$/, "Row $1: $2 (deposit/withdrawal) skipped"],
  [/^(.*): (.*) \(Ein-\/Auszahlung\) übersprungen$/, "$1: $2 (deposit/withdrawal) skipped"],
  [/^(.*): kein Text im PDF \(gescannt\?\)$/, "$1: no text in PDF (scanned?)"],
  [/^(.*): Anbieter nicht erkannt \(unterstützt: VIAC\)$/, "$1: provider not recognised (supported: VIAC)"],
  [/^(.*): Belegart nicht erkannt$/, "$1: document type not recognised"],
  [/^(.*): Datum nicht gefunden$/, "$1: date not found"],
  [/^(.*): (Börsenabrechnung|Ausschüttung|Zinsbeleg|Gebührenbeleg) nicht lesbar$/, "$1: $2 not readable"],
  [/^(.*): Börsenabrechnung ohne Betrag$/, "$1: trade confirmation without amount"],
  [/^(.*): Börsenabrechnung über CHF 0\.00 übersprungen$/, "$1: trade confirmation of CHF 0.00 skipped"],
  [/^(.*): Kontosaldierung ohne Zins\/Gebühr übersprungen$/, "$1: account closing without interest/fee skipped"],
  [/^(.*): Format nicht erkannt – CSV einzeln importieren und Spalten zuordnen$/, "$1: format not recognised – import the CSV on its own and map the columns"],
  [/^(.*): (Datei unlesbar|ZIP-Datei beschädigt)$/, "$1: file unreadable"],
  [/^Zu viele Dateien \(max\. (\d+)\)$/, "Too many files (max. $1)"],
];
function trErr(msg) {
  if (LANG !== "en" || !msg) return msg;
  if (EN_ERR[msg]) return EN_ERR[msg];
  for (const [rx, rep] of EN_ERR_RX) if (rx.test(msg)) return msg.replace(rx, rep);
  return msg;
}

/* Statische Texte im DOM übersetzen (und beim Zurückwechseln wiederherstellen) */
const ORIG_TEXT = new WeakMap();
const I18N_ATTRS = ["placeholder", "title", "aria-label"];
function translateDom(root = document.body) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.parentElement?.closest("script, style, [data-i18n-skip]")) continue;
    if (!ORIG_TEXT.has(n)) {
      const key = n.nodeValue.trim();
      if (!key || !(key in EN)) continue;
      ORIG_TEXT.set(n, n.nodeValue);
    }
    const orig = ORIG_TEXT.get(n), key = orig.trim();
    n.nodeValue = LANG === "en" ? orig.replace(key, EN[key]) : orig;
  }
  for (const el of root.querySelectorAll("[placeholder], [title], [aria-label]")) {
    for (const a of I18N_ATTRS) {
      const store = "i18n" + a.replace(/-./g, (m) => m[1].toUpperCase());
      if (!(store in el.dataset)) {
        const v = el.getAttribute(a);
        if (v == null || !(v in EN)) continue;
        el.dataset[store] = v;
      }
      el.setAttribute(a, LANG === "en" ? EN[el.dataset[store]] : el.dataset[store]);
    }
  }
  for (const el of root.querySelectorAll("[data-i18n-html]")) {
    if (!("i18nOrig" in el.dataset)) el.dataset.i18nOrig = el.innerHTML;
    el.innerHTML = LANG === "en" ? EN_HTML[el.dataset.i18nHtml] : el.dataset.i18nOrig;
  }
}

function setLang(lang) {
  LANG = lang === "en" ? "en" : "de";
  LOCALE = LANG === "en" ? "en-CH" : "de-CH";
  document.documentElement.lang = LANG;
  try { localStorage.setItem("fd-lang", LANG); } catch {}
  translateDom();
}
try { if (localStorage.getItem("fd-lang") === "en") setLang("en"); } catch {}
