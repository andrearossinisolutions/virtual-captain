"""Launcher: aggiorna il repo (git pull --ff-only) e poi avvia main.py con gli stessi argomenti.

Uso: python launcher.py [argomenti di main.py]   (es. --test-links, --host 127.0.0.1)
Se il pull fallisce (rete assente, modifiche locali, branch divergente) lo scrive e avvia comunque
la versione presente: non scarta mai modifiche locali.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


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


def main() -> int:
    update()
    return subprocess.call([sys.executable, os.path.join(HERE, "main.py"), *sys.argv[1:]], cwd=HERE)


if __name__ == "__main__":
    sys.exit(main())
