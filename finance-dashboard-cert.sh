#!/usr/bin/env bash
# Erstellt bzw. erneuert das selbstsignierte HTTPS-Zertifikat des Finanz-Dashboards.
# Browser (Chrome/Safari, v.a. auf Mac/iPhone) akzeptieren höchstens 398 Tage Laufzeit –
# deshalb 397 Tage und automatische Erneuerung 30 Tage vor Ablauf (systemd-Timer, wöchentlich).
set -euo pipefail
TLS_DIR=${TLS_DIR:-/etc/finance-dashboard/tls}
CERT="$TLS_DIR/cert.pem"; KEY="$TLS_DIR/key.pem"
DAYS=397
IP=$(hostname -I | awk '{print $1}')
NAME=$(hostname)

reason=""
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  reason="kein Zertifikat vorhanden"
else
  start=$(date -d "$(openssl x509 -in "$CERT" -noout -startdate | cut -d= -f2)" +%s)
  end=$(date -d "$(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2)" +%s)
  if [ $(( (end - start) / 86400 )) -gt 398 ]; then
    reason="Laufzeit zu lang für Browser (ERR_CERT_VALIDITY_TOO_LONG)"
  elif ! openssl x509 -in "$CERT" -noout -checkend $((30 * 86400)) >/dev/null; then
    reason="läuft in weniger als 30 Tagen ab"
  elif [ -n "$IP" ] && ! openssl x509 -in "$CERT" -noout -ext subjectAltName 2>/dev/null | grep -q "IP Address:$IP\b"; then
    reason="IP-Adresse hat sich geändert ($IP)"
  fi
fi
[ -z "$reason" ] && { echo "Zertifikat ok (gültig bis $(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2))"; exit 0; }

echo "→ HTTPS-Zertifikat neu erstellen: $reason"
mkdir -p "$TLS_DIR"
openssl req -x509 -newkey rsa:3072 -sha256 -days "$DAYS" -nodes \
  -keyout "$KEY.new" -out "$CERT.new" -subj "/CN=$NAME" \
  -addext "subjectAltName=IP:$IP,DNS:$NAME,DNS:$NAME.local" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" >/dev/null 2>&1
mv "$KEY.new" "$KEY"; mv "$CERT.new" "$CERT"
if getent group finance >/dev/null; then chown root:finance "$TLS_DIR" "$CERT" "$KEY"; fi
chmod 750 "$TLS_DIR"; chmod 640 "$KEY"; chmod 644 "$CERT"
echo "  gültig bis $(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2)"
if [ "${1:-}" != "--no-restart" ] && systemctl is-active --quiet finance-dashboard; then
  systemctl restart finance-dashboard
fi
