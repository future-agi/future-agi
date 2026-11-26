"""Spawn-safe TEST ONLY compiler; real PinCapture still owns the pin/lock."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SlowPinnedQuery:
    project_id: str
    manifest_id: str

    def __call__(self, manifest, definition):
        assert str(manifest.project_id) == self.project_id
        assert str(manifest.pk) == self.manifest_id
        # ClickHouse limits sleep to three seconds per block. Keep a real live
        # query for cancellation without failing before it enters execution.
        return "SELECT sleepEachRow(0.1) FROM numbers(30)", {}
