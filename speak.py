"""Voce locale: pyttsx3 (SAPI su Windows); in assenza stampa soltanto."""
try:
    import pyttsx3
    _e = pyttsx3.init()
except Exception:  # noqa: BLE001
    _e = None


def say(text: str):
    print(f"[CAPITANO] {text}", flush=True)
    if _e:
        _e.say(text)
        _e.runAndWait()
