"""Normalized source hashing for experiment dedup."""

import hashlib


def code_hash(text: str) -> str:
    """sha256 of source normalized for line endings and trailing whitespace."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    normalized = "\n".join(lines)
    return "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()
