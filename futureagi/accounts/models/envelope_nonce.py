"""Durable request nonces, accessed by the explicit (aud, id) SQL adapter.

Django 5.1 has no composite primary keys. This unmanaged projection is for
inspection only; DDL and all mutations use the real composite primary key.
"""

from django.db import models


class EnvelopeNonce(models.Model):
    aud = models.CharField(max_length=32)
    id = models.CharField(max_length=32, primary_key=True)
    exp = models.BigIntegerField(db_index=True)

    class Meta:
        managed = False
        db_table = "accounts_envelope_nonce"
