"""PDF-/ZIP-/CSV-Upload (z.B. VIAC-Belege): Text via pdftotext (Paket poppler-utils)."""
import base64
import hashlib
import io
import os
import subprocess
import zipfile

import importers


# --------------------------------------------------------------------------
# PDF-/ZIP-Import (z.B. VIAC-Belege): Text via pdftotext (Paket poppler-utils)
# --------------------------------------------------------------------------
MAX_DOCS = 3000


def pdf_text(data):
    try:
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"], input=data,
                           capture_output=True, timeout=30)
    except FileNotFoundError:
        raise ValueError("PDF-Import nicht verfügbar: pdftotext fehlt (install.sh erneut ausführen)")
    except subprocess.TimeoutExpired:
        return ""
    return r.stdout.decode("utf-8", "replace")


def parse_uploaded_files(files):
    """files: [{name, data(base64)}] mit PDFs, ZIPs (mit PDFs/CSVs) oder CSVs."""
    pdfs, csvs, seen = [], [], set()

    def add(name, raw):
        digest = hashlib.sha1(raw).hexdigest()
        if digest in seen:          # gleiche Datei doppelt ausgewählt
            return
        seen.add(digest)
        low = name.lower()
        if raw[:5] == b"%PDF-" or low.endswith(".pdf"):
            pdfs.append((name, raw))
        elif low.endswith((".csv", ".txt")):
            csvs.append((name, raw))

    for f in files:
        name = os.path.basename(str(f.get("name") or "datei"))
        try:
            raw = base64.b64decode(f.get("data") or "", validate=False)
        except ValueError:
            raise ValueError(f"{name}: Datei unlesbar")
        if raw[:4] == b"PK\x03\x04" or name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for info in z.infolist():
                        if info.is_dir() or info.file_size > 20_000_000 or "__MACOSX" in info.filename:
                            continue
                        if info.filename.lower().endswith((".pdf", ".csv", ".txt")):
                            add(os.path.basename(info.filename), z.read(info))
            except zipfile.BadZipFile:
                raise ValueError(f"{name}: ZIP-Datei beschädigt")
        else:
            add(name, raw)
        if len(pdfs) + len(csvs) > MAX_DOCS:
            raise ValueError(f"Zu viele Dateien (max. {MAX_DOCS})")
    if not pdfs and not csvs:
        raise ValueError("Keine PDF- oder CSV-Dateien gefunden")

    txs, skipped, providers = [], [], []
    if pdfs:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(2, min(8, os.cpu_count() or 2))) as ex:
            texts = list(ex.map(lambda p: pdf_text(p[1]), pdfs))
        docs = [(n, txt) for (n, _), txt in zip(pdfs, texts)]
        empty = [n for n, t in docs if not t.strip()]
        skipped += [f"{n}: kein Text im PDF (gescannt?)" for n in empty]
        t2, s2, prov = importers.parse_documents([d for d in docs if d[0] not in empty])
        txs += t2; skipped += s2; providers.append(prov)
    for n, raw in csvs:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:       # z.B. Excel-Export unter Windows
            text = raw.decode("cp1252", "replace")
        r = importers.parse(text)
        if r["needs_mapping"]:
            skipped.append(f"{n}: Format nicht erkannt – CSV einzeln importieren und Spalten zuordnen")
            continue
        txs += r["transactions"]; skipped += r["skipped"]; providers.append(r["provider"])
    txs.sort(key=lambda t: t["date"])
    prov = providers[0] if len(set(providers)) == 1 else "mixed"
    return dict(provider=prov, needs_mapping=False, transactions=txs, skipped=skipped, headers=[], sample=[],
                files=len(pdfs) + len(csvs))
