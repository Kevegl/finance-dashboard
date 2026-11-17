#!/usr/bin/env python3
"""Signaturprüfung des Update-Dienstes (finance-dashboard-update) mit echtem Git-Repo und SSH-Schlüsseln.

Aufruf:  python3 tests/update_verify.py      (braucht git >= 2.34 und ssh-keygen; ohne diese wird übersprungen)
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sh(*args, cwd=None, env=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r.stdout.strip()


def main():
    if not shutil.which("ssh-keygen") or not shutil.which("git"):
        print("übersprungen: git oder ssh-keygen fehlt")
        return
    tmp = Path(tempfile.mkdtemp(prefix="fd-sig-"))
    env = dict(os.environ, GIT_CONFIG_GLOBAL=str(tmp / "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
    os.environ.update({k: env[k] for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")})
    for name in ("good", "evil"):
        sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(tmp / name))
    signers = tmp / "allowed_signers"
    signers.write_text("# Kommentar\nrelease@example.invalid namespaces=\"git\" "
                       + (tmp / "good.pub").read_text().strip() + "\n")

    up, work, inst = tmp / "up.git", tmp / "work", tmp / "inst"
    sh("git", "init", "-q", "--bare", "-b", "main", str(up), env=env)
    sh("git", "clone", "-q", str(up), str(work), env=env)
    (work / "a.txt").write_text("1")
    sh("git", "add", "a.txt", cwd=work, env=env)
    sh("git", "commit", "-qm", "eins", cwd=work, env=env)
    sh("git", "push", "-q", "origin", "HEAD:main", cwd=work, env=env)
    sh("git", "clone", "-q", str(up), str(inst), env=env)          # «installierter» Stand

    def commit_and_tag(text, tag, key):
        (work / "a.txt").write_text(text)
        sh("git", "commit", "-qam", text, cwd=work, env=env)
        cfg = ["-c", "gpg.format=ssh", "-c", f"user.signingkey={tmp / key}.pub"] if key else []
        sh("git", *cfg, "tag", *(["-s"] if key else ["-a"]), tag, "-m", f"Release {tag}", cwd=work, env=env)
        sh("git", "push", "-q", "--follow-tags", "origin", "HEAD:main", cwd=work, env=env)

    loader = importlib.machinery.SourceFileLoader("fdupdate", str(ROOT / "finance-dashboard-update"))
    spec = importlib.util.spec_from_loader("fdupdate", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    def updater(channel, with_signers=True):
        os.environ.update(FD_ENV_FILE=str(tmp / "kein.env"), FD_REPO_DIR=str(inst), FD_UPDATE_CHANNEL=channel,
                          FD_UPDATE_SIGNERS=str(signers if with_signers else tmp / "fehlt"))
        u = mod.Updater()
        u.log = lambda msg: None
        return u

    # 1) Kanal release, Tag mit dem erlaubten Schlüssel signiert
    commit_and_tag("2", "v1.0.1", "good")
    sig = updater("release").inspect()["signature"]
    assert sig["required"] and sig["ok"], sig
    print("✓ signiertes Tag (erlaubter Schlüssel) wird akzeptiert:", sig.get("signer"))

    # 2) neueres Tag ohne Signatur -> abgelehnt, do_update bricht vor dem Installieren ab
    commit_and_tag("3", "v1.0.2", None)
    u = updater("release")
    info = u.inspect()
    assert info["target"] == "v1.0.2" and info["signature"] == {"required": True, "ok": False, "error": "nicht signiert"}, info
    head = sh("git", "rev-parse", "HEAD", cwd=inst)
    try:
        u.do_update()
        raise AssertionError("unsigniertes Update wurde nicht abgelehnt")
    except RuntimeError as e:
        assert "Signaturprüfung fehlgeschlagen" in str(e), e
    assert sh("git", "rev-parse", "HEAD", cwd=inst) == head, "Repository wurde trotz Ablehnung verändert"
    print("✓ unsigniertes Tag wird abgelehnt, nichts installiert")

    # 3) Tag mit fremdem Schlüssel -> abgelehnt
    commit_and_tag("4", "v1.0.3", "evil")
    sig = updater("release").inspect()["signature"]
    assert sig["required"] and not sig["ok"] and "allowed_signers" in sig["error"], sig
    print("✓ fremder Schlüssel wird abgelehnt:", sig["error"])

    # 4) Kanal main: unsignierter Commit abgelehnt, signierter akzeptiert
    sig = updater("main").inspect()["signature"]
    assert sig["required"] and not sig["ok"], sig
    (work / "a.txt").write_text("5")
    sh("git", "-c", "gpg.format=ssh", "-c", f"user.signingkey={tmp / 'good'}.pub", "commit", "-S", "-qam", "fünf",
       cwd=work, env=env)
    sh("git", "push", "-q", "origin", "HEAD:main", cwd=work, env=env)
    sig = updater("main").inspect()["signature"]
    assert sig["required"] and sig["ok"], sig
    print("✓ Kanal main: nur signierter neuester Commit wird akzeptiert")

    # 5) ohne allowed_signers: Prüfung aus (bisheriges Verhalten)
    sig = updater("release", with_signers=False).inspect()["signature"]
    assert sig == {"required": False, "ok": None}, sig
    print("✓ ohne allowed_signers bleibt die Prüfung aus")
    shutil.rmtree(tmp, ignore_errors=True)
    print("SIGNATUR-TEST OK")


if __name__ == "__main__":
    sys.exit(main())
