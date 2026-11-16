#!/usr/bin/env python3
"""Prüft den eigenen QR-Generator (app/static/qr.js): Codes erzeugen (Node) und mit einem unabhängigen Leser
(OpenCV) wieder einlesen. Läuft in GitHub Actions; OpenCV wird nur dort zum Testen installiert.

  pip install opencv-python-headless numpy && python3 tests/qr_roundtrip.py
"""
import json
import subprocess
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:
    print("OpenCV fehlt – Test übersprungen (pip install opencv-python-headless numpy)")
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent
GEN = r"""
const QR = require(process.argv[1]);
const texts = [
  "otpauth://totp/Finanz-Dashboard:demo?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Finanz-Dashboard&digits=6&period=30",
  "otpauth://totp/Finanz-Dashboard:J%C3%BCrg%20M%C3%BCller?secret=ABCDEFGHIJKLMNOPQRSTUVWXYZ234567&issuer=Finanz-Dashboard&digits=6&period=30",
  "A", "Grüezi Zürich äöü €"];
for (const n of [20, 45, 80, 120, 180, 250, 330, 400, 480, 580, 666])
  texts.push(Array.from({ length: n }, (_, i) => String.fromCharCode(97 + (i * 13 + n) % 26)).join(""));
console.log(JSON.stringify(texts.map((t) => ({ t, m: QR.matrix(t).map((r) => r.map((d) => (d ? 1 : 0)).join("")) }))));
"""


def main():
    cases = json.loads(subprocess.run(["node", "-e", GEN, str(ROOT / "app" / "static" / "qr.js")],
                                      capture_output=True, text=True, check=True).stdout)
    dets = [cv2.QRCodeDetector()] + ([cv2.QRCodeDetectorAruco()] if hasattr(cv2, "QRCodeDetectorAruco") else [])
    bad = []
    for c in cases:
        m = np.array([[0 if ch == "1" else 255 for ch in row] for row in c["m"]], dtype=np.uint8)
        m = np.pad(m, 4, constant_values=255)
        ok = any(d.detectAndDecode(cv2.resize(m, None, fx=f, fy=f, interpolation=cv2.INTER_NEAREST))[0] == c["t"]
                 for d in dets for f in (4, 6, 8))
        ver = (len(c["m"]) - 17) // 4
        print(f"{'✓' if ok else '✗'} Version {ver:2d}, {len(c['t'].encode()):3d} Bytes")
        if not ok:
            bad.append(ver)
    if bad:
        raise SystemExit(f"FEHLER: nicht lesbar bei Version {bad}")
    print("QR-TEST OK")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
