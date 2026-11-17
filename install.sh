#!/usr/bin/env bash
# Installation / Update des Finanz-Dashboards in einem Debian/Ubuntu-LXC (als root ausführen).
#   bash install.sh            -> installiert nach /opt/finance-dashboard, Port 8080
#   PORT=9000 bash install.sh  -> anderer Port
# Bei jedem Lauf bleiben Daten, Einstellungen, Passwort und Zertifikat erhalten.
set -euo pipefail

PORT="${PORT:-8080}"
BASE_CCY="${BASE_CCY:-CHF}"
DEST=/opt/finance-dashboard
ENV=/etc/finance-dashboard.env
TLS_DIR=/etc/finance-dashboard/tls
SRC="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausführen."; exit 1; }
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_SUSPEND=1   # während install.sh keine automatischen Neustarts – der Dienst wird unten selbst neu gestartet

echo "→ Pakete installieren"
apt-get update -qq
apt-get install -y -qq python3 ca-certificates tzdata openssl poppler-utils git >/dev/null

# --- Automatische Sicherheitsupdates für Debian (Python, OpenSSL, …) – einmalig einrichten.
#     Abschalten: AUTO_UPDATES=0 bash install.sh vor der ersten Einrichtung, oder später
#     /etc/apt/apt.conf.d/20auto-upgrades auf "0" setzen (wird danach nicht mehr überschrieben).
AUTO_MARK=/etc/finance-dashboard/.auto-updates
if [ "${AUTO_UPDATES:-1}" = 1 ] && [ ! -e "$AUTO_MARK" ]; then
  echo "→ Automatische Sicherheitsupdates einrichten (unattended-upgrades)"
  [ -e /etc/apt/apt.conf.d/20auto-upgrades ] || cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APT
  # Dienste nach Bibliotheks-Updates (z.B. OpenSSL, Python) automatisch neu starten
  mkdir -p /etc/needrestart/conf.d
  [ -e /etc/needrestart/conf.d/finance-dashboard.conf ] || cat > /etc/needrestart/conf.d/finance-dashboard.conf <<'NR'
# Finanz-Dashboard: Dienste nach Bibliotheks-Updates automatisch neu starten (ohne Rückfrage),
# den Update-Dienst des Dashboards aber nie (er installiert selbst Pakete und darf nicht abbrechen).
$nrconf{restart} = 'a';
$nrconf{kernelhints} = 0;
$nrconf{override_rc}{qr(^finance-dashboard-update)} = 0;
NR
  apt-get install -y -qq unattended-upgrades needrestart >/dev/null
  mkdir -p /etc/finance-dashboard && date -Iseconds > "$AUTO_MARK"
fi

echo "→ Benutzer und Verzeichnisse anlegen"
id finance >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin finance
mkdir -p "$DEST" /var/lib/finance-dashboard
rm -rf "$DEST/app"
cp -r "$SRC/app" "$DEST/"
chown -R finance:finance "$DEST" /var/lib/finance-dashboard
chmod 750 /var/lib/finance-dashboard

# --- Einstellungen (fehlende Werte werden ergänzt, vorhandene nie überschrieben)
FRESH=0; [ -f "$ENV" ] || FRESH=1
touch "$ENV"; chmod 600 "$ENV"
has() { grep -qE "^$1=" "$ENV"; }
has FD_PORT     || echo "FD_PORT=$PORT" >> "$ENV"
has FD_BASE_CCY || echo "FD_BASE_CCY=$BASE_CCY" >> "$ENV"
has FD_DATA_DIR || echo "FD_DATA_DIR=/var/lib/finance-dashboard" >> "$ENV"
# Neue Installationen erhalten nur veröffentlichte Versionen; bestehende behalten ihr Verhalten (main)
[ "$FRESH" = 1 ] && ! has FD_UPDATE_CHANNEL && echo "FD_UPDATE_CHANNEL=release" >> "$ENV"

# Anmeldung wird jetzt im Browser eingerichtet – alte Einträge aus früheren Versionen entfernen
sed -i '/^#\?FD_USER=/d; /^#\?FD_PASS=/d; /Passwortschutz/d; /^# Anmeldung (Benutzer/d' "$ENV"
rm -f /root/finance-dashboard-zugang.txt

# --- HTTPS: selbstsigniertes Zertifikat (397 Tage, wird automatisch erneuert)
IP=$(hostname -I | awk '{print $1}')
install -m 755 "$SRC/finance-dashboard-cert.sh" /usr/local/sbin/finance-dashboard-cert
TLS_DIR="$TLS_DIR" /usr/local/sbin/finance-dashboard-cert --no-restart
cat > /etc/systemd/system/finance-dashboard-cert.service <<UNIT
[Unit]
Description=Finanz-Dashboard: HTTPS-Zertifikat prüfen/erneuern

[Service]
Type=oneshot
Environment=TLS_DIR=$TLS_DIR
ExecStart=/usr/local/sbin/finance-dashboard-cert
UNIT
cat > /etc/systemd/system/finance-dashboard-cert.timer <<UNIT
[Unit]
Description=Finanz-Dashboard: Zertifikat wöchentlich prüfen

[Timer]
OnCalendar=weekly
Persistent=true

[Install]
WantedBy=timers.target
UNIT
has FD_CERT || echo "FD_CERT=$TLS_DIR/cert.pem" >> "$ENV"
has FD_KEY  || echo "FD_KEY=$TLS_DIR/key.pem" >> "$ENV"

# --- Updates über das Dashboard (Einstellungen → Updates), nur bei Installation per git clone
install -m 755 "$SRC/finance-dashboard-update" /usr/local/sbin/finance-dashboard-update
mkdir -p /var/lib/finance-dashboard-update && chown root:root /var/lib/finance-dashboard-update && chmod 755 /var/lib/finance-dashboard-update
sed -i '/^FD_REPO_DIR=/d' "$ENV"
if git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "→ Update-Dienst einrichten (Repository: $SRC)"
  echo "FD_REPO_DIR=$SRC" >> "$ENV"
  cat > /etc/systemd/system/finance-dashboard-update.service <<UNIT
[Unit]
Description=Finanz-Dashboard: Update-Auftrag aus dem Dashboard ausführen

[Service]
Type=oneshot
Environment=HOME=/root
ExecStart=/usr/local/sbin/finance-dashboard-update request
TimeoutStartSec=30min
UNIT
  cat > /etc/systemd/system/finance-dashboard-update.path <<UNIT
[Unit]
Description=Finanz-Dashboard: auf Update-Aufträge aus dem Dashboard warten

[Path]
PathExists=/var/lib/finance-dashboard/update-request
Unit=finance-dashboard-update.service

[Install]
WantedBy=paths.target
UNIT
  cat > /etc/systemd/system/finance-dashboard-update-check.service <<UNIT
[Unit]
Description=Finanz-Dashboard: auf neue Version prüfen

[Service]
Type=oneshot
Environment=HOME=/root
ExecStart=/usr/local/sbin/finance-dashboard-update check
TimeoutStartSec=10min
UNIT
  cat > /etc/systemd/system/finance-dashboard-update-check.timer <<UNIT
[Unit]
Description=Finanz-Dashboard: täglich auf neue Version prüfen

[Timer]
OnCalendar=daily
RandomizedDelaySec=2h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  UPDATER=1
else
  echo "→ Kein Git-Repository – Updates über das Dashboard bleiben deaktiviert (Installation per git clone, siehe README)"
  systemctl disable --now finance-dashboard-update.path finance-dashboard-update-check.timer >/dev/null 2>&1 || true
  UPDATER=0
fi

echo "→ systemd-Dienst einrichten"
cp "$SRC/finance-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable finance-dashboard >/dev/null 2>&1
systemctl enable --now finance-dashboard-cert.timer >/dev/null 2>&1
if [ "$UPDATER" = 1 ]; then
  systemctl enable --now finance-dashboard-update.path finance-dashboard-update-check.timer >/dev/null 2>&1
fi

# Antwortet das Dashboard? (Dienst aktiv und /api/health ok; wartet bis zu 20 s)
dashboard_ok() {
  local i port
  port=$(grep -E '^FD_PORT=' "$ENV" | cut -d= -f2)
  for i in $(seq 1 20); do
    sleep 1
    systemctl is-active --quiet finance-dashboard || continue
    python3 - "$port" <<'PY' && return 0
import json, ssl, sys, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
for scheme in ("https", "http"):
    try:
        with urllib.request.urlopen(f"{scheme}://127.0.0.1:{sys.argv[1]}/api/health", timeout=3, context=ctx) as r:
            sys.exit(0 if json.load(r).get("ok") else 1)
    except Exception:
        pass
sys.exit(1)
PY
  done
  return 1
}

COMPAT=/etc/systemd/system/finance-dashboard.service.d/container-compat.conf
systemctl restart finance-dashboard
if ! dashboard_ok && [ ! -e "$COMPAT" ]; then
  # Manche Container (z.B. ältere oder privilegierte LXC) unterstützen nicht alle Härtungs-Optionen.
  # Dann nur diese lockern – die Grundhärtung (eigener Benutzer, Dateisystem nur lesbar) bleibt.
  echo "⚠ Dienst startet mit der erweiterten Härtung nicht – lockere container-abhängige Optionen:"
  journalctl -u finance-dashboard -n 8 --no-pager || true
  mkdir -p "$(dirname "$COMPAT")"
  cat > "$COMPAT" <<'UNIT'
# Von install.sh angelegt, weil der Dienst mit der vollen Härtung in diesem Container nicht startete.
# Löschen und install.sh erneut ausführen, um die volle Härtung nochmals zu versuchen.
[Service]
PrivateDevices=false
ProtectKernelTunables=false
ProtectKernelModules=false
ProtectKernelLogs=false
ProtectControlGroups=false
ProtectHostname=false
ProtectClock=false
ProtectProc=default
ProcSubset=all
RestrictNamespaces=false
MemoryDenyWriteExecute=false
SystemCallFilter=
UNIT
  systemctl daemon-reload
  systemctl restart finance-dashboard
fi
dashboard_ok || { echo "✗ Dienst läuft nicht:"; journalctl -u finance-dashboard -n 20 --no-pager; exit 1; }
[ -e "$COMPAT" ] && echo "  Hinweis: eingeschränkte Härtung aktiv ($COMPAT)"

# Gleich einmal auf Updates prüfen, damit die Einstellungen einen Stand anzeigen (nicht während eines Updates)
if [ "$UPDATER" = 1 ] && [ -z "${FD_UPDATER_RUNNING:-}" ]; then
  systemctl start --no-block finance-dashboard-update-check.service || true
fi

PORT_NOW=$(grep -E '^FD_PORT=' "$ENV" | cut -d= -f2)
echo
echo "✓ Fertig: https://$IP:$PORT_NOW"
echo
echo "  Der Browser warnt beim ersten Aufruf vor dem selbstsignierten Zertifikat –"
echo "  einmal 'Erweitert → Weiter zu …' wählen (oder das Zertifikat als vertrauenswürdig importieren)."
echo "  Beim ersten Aufruf legst du Benutzername und Passwort fest – am besten gleich jetzt."
DATA_NOW=$(grep -E '^FD_DATA_DIR=' "$ENV" | cut -d= -f2)
if [ -f "${DATA_NOW:-/var/lib/finance-dashboard}/setup-code.txt" ]; then
  echo "  Einrichtungscode dafür: $(cat "${DATA_NOW:-/var/lib/finance-dashboard}/setup-code.txt")"
  echo "  (steht auch in ${DATA_NOW:-/var/lib/finance-dashboard}/setup-code.txt und in journalctl -u finance-dashboard)"
fi
echo "  Passwort vergessen?  cd $DEST/app && FD_DATA_DIR=/var/lib/finance-dashboard python3 server.py --reset-password"
echo "  Einstellungen: $ENV   Daten: /var/lib/finance-dashboard/finance.db"
