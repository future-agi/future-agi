"""Bounded timing observation of the disposable supervisor, not product policy."""

import os
from functools import wraps
from threading import Lock
from time import monotonic

from run import save


class OperationProfile:
    def __init__(self, path, *, clock=monotonic, write=save):
        self.path, self.clock, self.write = path, clock, write
        self.started = self.last_saved = clock()
        self.counts = {}
        self.lock = Lock()

    def wrap(self, owner, name, label):
        if label in self.counts or len(self.counts) >= 8:
            raise ValueError("duplicate or excessive diagnostic labels")
        self.counts[label] = {
            "calls": 0,
            "errors": 0,
            "seconds": 0.0,
            "max_seconds": 0.0,
        }
        original = getattr(owner, name)

        @wraps(original)
        def observed(*args, **kwargs):
            started, failed = self.clock(), False
            try:
                return original(*args, **kwargs)
            except BaseException:
                failed = True
                raise
            finally:
                elapsed = self.clock() - started
                with self.lock:
                    count = self.counts[label]
                    count["calls"] += 1
                    count["errors"] += int(failed)
                    count["seconds"] += elapsed
                    count["max_seconds"] = max(count["max_seconds"], elapsed)
                    now = self.clock()
                    if now - self.last_saved >= 2:
                        # Periodic snapshots survive SIGTERM without installing
                        # a signal handler or changing controller shutdown.
                        try:
                            self.write(
                                self.path,
                                {
                                    "pid": os.getpid(),
                                    "elapsed_seconds": now - self.started,
                                    "inclusive_timings": True,
                                    "operations": self.counts,
                                },
                            )
                        except OSError:
                            # Missing diagnostic output cannot change a result
                            # or replace the original exception. It is not proof.
                            pass
                        self.last_saved = now

        setattr(owner, name, observed)


def install(run):
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        CheckedInPropertyCatalogDevRuntime,
    )
    from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
        DurableNativeCatalogWriter,
    )
    from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
        NativeWriteProof,
    )

    profile = OperationProfile(run.directory / f"operation-profile-{os.getpid()}.json")
    for owner, method, label in (
        (NativeWriteProof, "attest", "native_attest"),
        (NativeWriteProof, "cover", "native_coverage"),
        (NativeWriteProof, "agreed_read", "native_agreed_read"),
        (DurableNativeCatalogWriter, "insert", "native_insert"),
        (CheckedInPropertyCatalogDevRuntime, "_prepare_revision", "prepare_revision"),
        (CheckedInPropertyCatalogDevRuntime, "activate", "activate"),
    ):
        profile.wrap(owner, method, label)
    return profile
