"""Envelope encryption with one data key per user, blind indexes, and crypto-shredding.

- Master key (KEK) comes from a KeyProvider: env var for dev, a KMS/HSM located in Egypt in prod.
- Each user gets a random 256-bit data key (DEK), stored ONLY wrapped (encrypted) by the KEK.
- Fields are sealed with AES-256-GCM; the AAD binds a ciphertext to its user + field, so a
  value copied into another user's row or another column fails to decrypt.
- Blind index = HMAC-SHA256 with a per-user key derived from the DEK: exact-match lookups
  (phone number, entity name) without decrypting.
- Crypto-shredding: destroy the wrapped DEK -> every ciphertext of that user (backups included)
  becomes permanently unreadable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = b"\x01"
_NONCE = 12


class CryptoError(Exception):
    """Wrong key, tampered ciphertext, or a shredded user."""


class KeyProvider(Protocol):
    def wrap(self, dek: bytes, *, user_id: uuid.UUID) -> bytes: ...

    def unwrap(self, wrapped: bytes, *, user_id: uuid.UUID) -> bytes: ...


class LocalKeyProvider:
    """KEK from an env var (base64, 32 bytes). Dev/test only; production uses a KMS/HSM in Egypt."""

    def __init__(self, master_key_b64: str) -> None:
        key = base64.b64decode(master_key_b64)
        if len(key) != 32:
            raise ValueError("MASTER_KEY must be 32 bytes, base64-encoded")
        self._kek = AESGCM(key)

    def wrap(self, dek: bytes, *, user_id: uuid.UUID) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + self._kek.encrypt(nonce, dek, b"dek:" + user_id.bytes)

    def unwrap(self, wrapped: bytes, *, user_id: uuid.UUID) -> bytes:
        try:
            return self._kek.decrypt(wrapped[:_NONCE], wrapped[_NONCE:], b"dek:" + user_id.bytes)
        except Exception as exc:  # cryptography raises InvalidTag
            raise CryptoError("cannot unwrap data key") from exc


def new_data_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


class UserCipher:
    """Seals/opens field values for ONE user."""

    def __init__(self, user_id: uuid.UUID, dek: bytes) -> None:
        self._user = user_id
        self._aead = AESGCM(dek)
        self._index_key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"nasem-blind-index-v1"
        ).derive(dek)

    def _aad(self, field: str) -> bytes:
        return self._user.bytes + b"|" + field.encode()

    def seal(self, plaintext: str, *, field: str) -> str:
        nonce = os.urandom(_NONCE)
        ct = self._aead.encrypt(nonce, plaintext.encode(), self._aad(field))
        return base64.urlsafe_b64encode(_VERSION + nonce + ct).decode()

    def open(self, token: str, *, field: str) -> str:
        try:
            raw = base64.urlsafe_b64decode(token.encode())
            if raw[:1] != _VERSION:
                raise CryptoError("unknown ciphertext version")
            return self._aead.decrypt(raw[1 : 1 + _NONCE], raw[1 + _NONCE :], self._aad(field)).decode()
        except CryptoError:
            raise
        except Exception as exc:
            raise CryptoError("cannot decrypt field") from exc

    def blind_index(self, value: str, *, field: str) -> str:
        normalized = " ".join(value.split()).casefold()
        mac = hmac.new(self._index_key, field.encode() + b"|" + normalized.encode(), hashlib.sha256)
        return mac.hexdigest()
