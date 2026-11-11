"""Regola del firewall di Windows: consente la porta della pagina solo alla rete Tailscale.

Vale per l'intervallo 100.64.0.0/10 (non per un IP preciso), quindi non va rifatta se l'IP cambia.
Serve il permesso di amministratore: parte la finestra UAC di Windows, una volta sola.
"""
from __future__ import annotations

import os
import subprocess
import time


def _name(port: int) -> str:
    return f"Virtual Captain {port} (Tailscale)"


def _rule_args(port: int) -> str:
    return (f'advfirewall firewall add rule name="{_name(port)}" dir=in action=allow '
            f"protocol=TCP localport={port} remoteip=100.64.0.0/10")


def manual_command(port: int) -> str:
    return f"netsh {_rule_args(port)}   (da PowerShell come amministratore)"


def rule_exists(port: int) -> bool:
    r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={_name(port)}"],
                       capture_output=True, text=True)
    return r.returncode == 0


def ensure(port: int) -> bool:
    """True se la regola c'è (o non serve). Su Windows, se manca, chiede il permesso e la crea."""
    if os.name != "nt":
        return True
    try:
        if rule_exists(port):
            return True
        print(f"\nIl firewall di Windows blocca la porta {port} per il tablet.")
        if input("Creo la regola (solo rete Tailscale)? Windows chiederà il permesso. [S/n] ").strip().lower() in ("n", "no"):
            print("Ok. Per farlo a mano:", manual_command(port))
            return False
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", _rule_args(port), None, 0)
        for _ in range(10):  # aspetta che la finestra UAC venga confermata
            time.sleep(1)
            if rule_exists(port):
                print("Regola creata.")
                return True
        print("Regola non creata (permesso negato?). Per farlo a mano:", manual_command(port))
        return False
    except Exception as e:  # noqa: BLE001
        print("Firewall: impossibile controllare la regola:", e)
        return False
