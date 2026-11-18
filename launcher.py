"""Launcher: aggiorna il repo (git pull --ff-only) e poi avvia main.py con gli stessi argomenti.

Uso: python launcher.py [argomenti di main.py]   (es. --test-links, --host 127.0.0.1)
Se il pull fallisce (rete assente, modifiche locali, branch divergente) lo scrive e avvia comunque
la versione presente: non scarta mai modifiche locali.
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.join(HERE, "requirements.txt")
REQ_HASH = os.path.join(HERE, ".requirements.sha256")


def update() -> bool:
    try:
        r = subprocess.run(["git", "pull", "--ff-only"], cwd=HERE, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[launcher] aggiornamento non riuscito ({e}): avvio la versione presente")
        return False
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        print(f"[launcher] git pull non riuscito, avvio la versione presente:\n{out}")
        return False
    print(f"[launcher] {out.splitlines()[-1] if out else 'ok'}")
    return True


def install_requirements() -> bool:
    """Installa requirements.txt se e' cambiato dall'ultima installazione riuscita. Non blocca mai l'avvio."""
    if not os.path.isfile(REQ):
        return True
    try:
        with open(REQ, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        try:
            with open(REQ_HASH, "r", encoding="utf-8") as f:
                if f.read().strip() == digest:
                    print("[launcher] dipendenze gia' a posto")
                    return True
        except OSError:
            pass
        print("[launcher] installo le dipendenze...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", REQ],
                           cwd=HERE, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            lines = (r.stdout + r.stderr).strip().splitlines()
            print(f"[launcher] pip non riuscito: {lines[-1] if lines else 'codice ' + str(r.returncode)}")
            return False
        with open(REQ_HASH, "w", encoding="utf-8") as f:
            f.write(digest)
        print("[launcher] dipendenze aggiornate")
        return True
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[launcher] pip non riuscito ({e}): avvio comunque")
        return False


def main() -> int:
    update()
    install_requirements()
    return subprocess.call([sys.executable, os.path.join(HERE, "main.py"), *sys.argv[1:]], cwd=HERE)


if __name__ == "__main__":
    sys.exit(main())
