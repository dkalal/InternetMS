"""Encryption helpers for Technician payout account numbers."""

from django.conf import settings


class PayoutAccountEncryptionUnavailable(RuntimeError):
    """Raised when the separate payout-account key ring is missing or invalid."""


def payout_account_cipher():
    try:
        from cryptography.fernet import Fernet, MultiFernet
    except ImportError as exc:
        raise PayoutAccountEncryptionUnavailable(
            "The payout account encryption library is not installed."
        ) from exc
    configured_keys = getattr(settings, "TECHNICIAN_PAYOUT_ACCOUNT_ENCRYPTION_KEYS", ())
    if isinstance(configured_keys, str):
        configured_keys = [key.strip() for key in configured_keys.split(",") if key.strip()]
    if not configured_keys:
        raise PayoutAccountEncryptionUnavailable(
            "Technician payout account encryption is not configured."
        )
    try:
        return MultiFernet([Fernet(key.encode("ascii")) for key in configured_keys])
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise PayoutAccountEncryptionUnavailable(
            "Technician payout account encryption keys are invalid."
        ) from exc


def encrypt_payout_account_number(account_number: str) -> str:
    return payout_account_cipher().encrypt(account_number.encode("utf-8")).decode("ascii")


def rotate_payout_account_ciphertext(ciphertext: str) -> tuple[str, bool]:
    """Return ciphertext under the primary key and whether rotation was needed."""
    cipher = payout_account_cipher()
    from cryptography.fernet import Fernet, InvalidToken
    configured_keys = getattr(settings, "TECHNICIAN_PAYOUT_ACCOUNT_ENCRYPTION_KEYS", ())
    if isinstance(configured_keys, str):
        configured_keys = [key.strip() for key in configured_keys.split(",") if key.strip()]
    token = ciphertext.encode("ascii")
    try:
        Fernet(configured_keys[0].encode("ascii")).decrypt(token)
        return ciphertext, False
    except InvalidToken:
        return cipher.rotate(token).decode("ascii"), True
