"""``webhooks_reencrypt``: rewrite every webhooks ciphertext under the newest ``WEBHOOKS_ENCRYPTION_KEYS`` entry, in
chunks, and report how many are left on older keys (DESIGN.md section 5.3, K2; WT-27). A key is removed from the list
only when that count is 0.

The columns: the endpoint's URL, custom headers, GitHub token and legacy signing key, and every secret. A ciphertext
that no key of the list decrypts is counted as ``undecryptable`` (WA6) and left as it is, never overwritten. No value,
key or plaintext is logged or returned: only counts.
"""

from __future__ import annotations

import logging

from cryptography.fernet import InvalidToken
from django.db import transaction

from webhooks import crypto
from webhooks.models import Endpoint, Secret

log = logging.getLogger("webhooks.reencrypt")

COLUMNS = (
    (
        Endpoint,
        (
            "url_encrypted",
            "headers_encrypted",
            "github_token_encrypted",
            "legacy_signing_key_encrypted",
        ),
    ),
    (Secret, ("secret_encrypted",)),
)
CHUNK = 500


def _rows(model, cols, after: str, chunk: int):
    return list(
        model.objects.select_for_update(skip_locked=True)
        .filter(pk__gt=after)
        .order_by("pk")
        .only("pk", *cols)[:chunk]
    )


def count_left() -> dict[str, int]:
    """Ciphertexts not on the newest key, per table and column, and in all (``total``)."""
    out: dict[str, int] = {}
    for model, cols in COLUMNS:
        for col in cols:
            n = sum(
                1
                for blob in model.objects.filter(**{f"{col}__isnull": False})
                .values_list(col, flat=True)
                .iterator(chunk_size=CHUNK)
                if not crypto.on_newest_key(blob)
            )
            out[f"{model._meta.db_table}.{col}"] = n
    out["total"] = sum(out.values())
    return out


def run(chunk: int = CHUNK, dry_run: bool = False) -> dict:
    """Rewrite what is on an older key; returns ``rewritten``, ``undecryptable`` and ``left`` (from a fresh count)."""
    rewritten = undecryptable = 0
    if not dry_run:
        for model, cols in COLUMNS:
            after = ""
            while True:
                with transaction.atomic():
                    rows = _rows(model, cols, after, chunk)
                    if not rows:
                        break
                    for row in rows:
                        fields = {}
                        for col in cols:
                            blob = getattr(row, col)
                            if blob is None or crypto.on_newest_key(blob):
                                continue
                            try:
                                fields[col] = crypto.rotate(blob)
                            except InvalidToken:
                                undecryptable += 1
                        if fields:
                            model.objects.filter(pk=row.pk).update(**fields)
                            rewritten += len(fields)
                    after = rows[-1].pk
    left = count_left()
    out = {"rewritten": rewritten, "undecryptable": undecryptable, "left": left}
    log.info(
        "webhooks re-encryption pass",
        extra={
            "metric": "webhooks.reencrypt",
            "rewritten": rewritten,
            "undecryptable": undecryptable,
            "left": left["total"],
        },
    )
    return out
