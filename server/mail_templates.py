"""Plain-text mail templates (English). Links carry the token in the fragment."""
from __future__ import annotations

SIGNATURE = "\n--\nVirtual Captain\n"


def verify_mail(base_url: str, token: str):
    return ("Confirm your Virtual Captain account",
            "Welcome aboard.\n\nConfirm your email address and choose your password within "
            "24 hours:\n\n"
            f"{base_url}/verify#{token}\n\n"
            "If you did not create an account, ignore this message." + SIGNATURE)


def already_registered_mail():
    return ("Virtual Captain: sign-up attempt",
            "Someone tried to register a Virtual Captain account with this email address, "
            "which already has one.\n\nIf it was you, simply log in, or use the password "
            "reset option if you forgot your password. If it was not you, you can ignore "
            "this message: nothing has changed." + SIGNATURE)


def reset_mail(base_url: str, token: str):
    return ("Reset your Virtual Captain password",
            "A password reset was requested for your account. The link below works once "
            f"and expires in 1 hour:\n\n{base_url}/reset#{token}\n\n"
            "If you did not ask for it, ignore this message: your password is unchanged."
            + SIGNATURE)


def email_change_mail(base_url: str, token: str):
    return ("Confirm your new Virtual Captain email address",
            "Confirm this address within 24 hours to use it for your Virtual Captain "
            f"account:\n\n{base_url}/verify#{token}\n\n"
            "If you did not ask for this, ignore this message." + SIGNATURE)
