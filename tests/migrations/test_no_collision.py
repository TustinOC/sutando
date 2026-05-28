"""CI guard: prevent migration number collisions.

Fails if the working tree adds a migration script whose number is
already used by a migration in this directory (same-PR collision
or merge-conflict class). On an empty migrations/ dir, this test
is always green.

Run as part of the test suite on every PR.
"""
from __future__ import annotations

import re
from pathlib import Path
from collections import Counter

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
_MIGRATION_RE = re.compile(r'^(\d{4})-(.+)\.(sh|py)$')


def test_no_duplicate_migration_numbers() -> None:
    """Each migration number must be unique."""
    if not MIGRATIONS_DIR.exists():
        return  # nothing to check on a fresh repo

    numbers: list[int] = []
    for entry in MIGRATIONS_DIR.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            numbers.append(int(m.group(1)))

    counts = Counter(numbers)
    duplicates = {n: c for n, c in counts.items() if c > 1}
    assert not duplicates, (
        f"Duplicate migration numbers detected: "
        + ", ".join(f"{n} (×{c})" for n, c in sorted(duplicates.items()))
        + ". Each migration must have a unique 4-digit number."
    )


def test_migration_numbers_are_sequential() -> None:
    """Migration numbers must form a gapless sequence starting at 0001 (if any exist)."""
    if not MIGRATIONS_DIR.exists():
        return

    numbers = sorted(
        int(_MIGRATION_RE.match(e.name).group(1))
        for e in MIGRATIONS_DIR.iterdir()
        if _MIGRATION_RE.match(e.name)
    )
    if not numbers:
        return

    expected = list(range(1, len(numbers) + 1))
    assert numbers == expected, (
        f"Migration numbers are not sequential. Got {numbers}, expected {expected}. "
        "New migrations must use number max(existing)+1."
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
