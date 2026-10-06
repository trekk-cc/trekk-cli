"""One-way, salted identity of an API key: a checkpoint records which keys Trigify confirmed for
its workspace without ever holding a key (scrypt makes guessing a short key slow)."""

import hashlib
import os

SALT_BYTES = 16


def new_salt() -> bytes:
    return os.urandom(SALT_BYTES)


def fingerprint(key: str, salt: bytes) -> str:
    """The key's scrypt hash under `salt`, as 64 hex characters. n/r/p/dklen are fixed: stored
    checkpoints hold fingerprints made with them, and changing them invalidates every one."""
    return hashlib.scrypt(key.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()
