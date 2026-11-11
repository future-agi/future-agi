"""Compatibility shim: the parser now lives in the seccomp media worker.

``probe(path)`` is an in-process parser double for unit tests only. Production
validation always runs it through ``sandbox.probe`` in a confined process.
"""

from tracer.services.attachments.media_worker import probe

__all__ = ["probe"]
