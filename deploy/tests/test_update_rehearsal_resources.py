"""Daemon-free tests for the native normal-bootstrap resource producer."""
from __future__ import annotations

import copy
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import update_rehearsal_resources as resources


PLAN = [
    {"app_label": "tracer", "name": "0117_before_envelope"},
    {"app_label": "tracer", "name": "0118_sharedlink_error_feed_issue_resource_type"},
]
TABLES = [
    {"engine": "postgres", "table": "public.trace_events", "reference_rows": 100000,
     "reference_bytes": 4096000},
]
MIGRATION_TABLES = [
    {"app_label": "tracer", "name": "0117_before_envelope", "tables": []},
    {"app_label": "tracer", "name": "0118_sharedlink_error_feed_issue_resource_type",
     "tables": [{"engine": "postgres", "table": "public.trace_events"}]},
]
SAMPLES = [
    {"monotonic": 10.0, "boundary": "before_start", "disk_bytes": 10000,
     "wal_bytes": 2000, "temp_bytes": 0, "memory_bytes": 0},
    {"monotonic": 11.0, "boundary": "during", "disk_bytes": 11500,
     "wal_bytes": 5000, "temp_bytes": 900, "memory_bytes": 720000},
    {"monotonic": 12.0, "boundary": "after_end", "disk_bytes": 11000,
     "wal_bytes": 4200, "temp_bytes": 500, "memory_bytes": 640000},
]


def envelope_fixture(plan=PLAN, tables=TABLES, migration_tables=MIGRATION_TABLES, samples=SAMPLES):
    start, end = 10.25, 11.75
    return resources.reduce_envelope(plan, tables, migration_tables, copy.deepcopy(samples), end - start,
                                     started_monotonic=start, completed_monotonic=end)


class FixedSampler(resources.NormalBootstrapEnvelopeSampler):
    """Use fixed daemon-free counter fixtures while exercising the public bracket."""

    def __init__(self, *, pending_plan=PLAN, tables=TABLES, migration_tables=MIGRATION_TABLES,
                 interval_seconds=0.01):
        (self._bound_plan, self._bound_tables, self._bound_migration_tables,
         self._bound_plan_hash, self._bound_tables_hash,
         self._bound_migration_tables_hash) = resources.validate_plan_and_tables(
            pending_plan, tables, migration_tables)
        self.snapshot_generation = "a" * 64
        self.target_digest = "sha256:" + "b" * 64
        self._identity_public = {"daemon_id": "fixture-daemon", "target_digest": self.target_digest}
        self._identity_sha256 = resources._hash(self._identity_public)
        self.clock = time.monotonic
        self.interval = interval_seconds
        self._used = False
        self._samples = []
        import threading
        self._sample_lock = threading.Lock()
        self._sample_error = None
        self._stop = threading.Event()
        self._last_pids = {}
        self.read_count = 0

    def _read(self):
        self.read_count += 1
        n = self.read_count
        return {
            "monotonic": self.clock(),
            "disk_bytes": 10000 + 100 * n,
            "wal_bytes": 2000 + 50 * n,
            "temp_bytes": 25 * n,
            "memory_bytes": 100000 * n,
        }

    def _verify_identity(self, *, check_ready, check_daemon):
        return {}


class ResourceEnvelopeTests(unittest.TestCase):
    def test_postgres_global_and_database_spill_directories_are_measured(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('pg_wal/archive_status','base/pgsql_tmp/shared.fileset','base/1/pgsql_tmp','pg_tblspc'):
                (root/name).mkdir(parents=True)
            paths = [root/name for name in ('pg_wal/00000001','base/pgsql_tmp/spill',
                'base/pgsql_tmp/shared.fileset/spill','base/1/pgsql_tmp/spill')]
            for path in paths: path.write_bytes(b'x'*8192)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                wal, temporary_bytes = resources._postgres_resource_bytes(fd, [0])
            finally:
                os.close(fd)
            self.assertEqual(wal, paths[0].stat().st_blocks*512)
            self.assertEqual(temporary_bytes, sum(path.stat().st_blocks*512 for path in paths[1:]))

    def test_owned_footprint_counts_link_and_special_inodes_without_following_or_opening(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owned = root/'owned'; owned.mkdir()
            outside = root/'outside'; outside.write_bytes(b'x'*65536)
            payload = owned/'payload'; payload.write_bytes(b'data'*2048)
            os.link(payload, owned/'hardlink')
            link = owned/'clickhouse-link'; link.symlink_to(outside)
            fifo = owned/'supervisor-pipe'; os.mkfifo(fifo)
            expected = sum(path.lstat().st_blocks*512 for path in (owned, payload, link, fifo))
            fd = os.open(owned, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with patch.object(resources.os, 'open', side_effect=AssertionError('leaf must not be opened')):
                    actual = resources._owned_allocated_tree(fd, [0], set())
            finally:
                os.close(fd)
            self.assertEqual(actual, expected)


    def test_reducer_preserves_full_envelope_and_physical_peaks_without_rate(self):
        measured = envelope_fixture()
        self.assertEqual(set(measured), {
            "measurement_basis", "pending_plan", "migration_tables", "tables", "duration_seconds",
            "peak_disk_growth_bytes", "wal_bytes", "temp_bytes", "peak_memory_bytes",
        })
        self.assertEqual(measured["measurement_basis"], "normal-bootstrap-envelope-v1")
        self.assertEqual(measured["migration_tables"], MIGRATION_TABLES)
        self.assertEqual(measured["duration_seconds"], 1.5)
        self.assertEqual(measured["peak_disk_growth_bytes"], 1500)
        self.assertEqual(measured["wal_bytes"], 5000)
        self.assertEqual(measured["temp_bytes"], 900)
        self.assertEqual(measured["peak_memory_bytes"], 720000)
        self.assertNotIn("throughput", measured)
        self.assertNotIn("processed_bytes", measured)

    def test_physical_wal_temp_can_fall_and_peak_not_delta_is_retained(self):
        samples = [
            {"monotonic": 1.0, "boundary": "before_start", "disk_bytes": 500,
             "wal_bytes": 700, "temp_bytes": 600, "memory_bytes": 5},
            {"monotonic": 2.0, "boundary": "during", "disk_bytes": 510,
             "wal_bytes": 400, "temp_bytes": 200, "memory_bytes": 10},
            {"monotonic": 3.0, "boundary": "after_end", "disk_bytes": 505,
             "wal_bytes": 300, "temp_bytes": 0, "memory_bytes": 8},
        ]
        measured = resources.reduce_envelope([], [], [], samples, 1.5,
                                             started_monotonic=1.25, completed_monotonic=2.75)
        self.assertEqual(measured["wal_bytes"], 700)
        self.assertEqual(measured["temp_bytes"], 600)
        self.assertEqual(measured["peak_disk_growth_bytes"], 10)

    def test_empty_plan_requires_real_sampled_envelope_but_no_table_references(self):
        measured = resources.reduce_envelope([], [], [], copy.deepcopy(SAMPLES), 1.5,
                                             started_monotonic=10.25, completed_monotonic=11.75)
        self.assertEqual(measured["pending_plan"], [])
        self.assertEqual(measured["migration_tables"], [])
        self.assertEqual(measured["tables"], [])
        self.assertGreater(measured["duration_seconds"], 0)
        with self.assertRaises(resources.ResourceObservationError):
            resources.validate_plan_and_tables([], TABLES, [])

    def test_missing_counter_extra_fields_and_sample_gap_refuse(self):
        missing = copy.deepcopy(SAMPLES)
        del missing[1]["memory_bytes"]
        extra = copy.deepcopy(SAMPLES)
        extra[1]["processed_bytes"] = 10
        for samples in (missing, extra):
            with self.subTest(samples=samples), self.assertRaises(resources.ResourceObservationError):
                resources.reduce_envelope(PLAN, TABLES, MIGRATION_TABLES, samples, 1.5,
                                          started_monotonic=10.25, completed_monotonic=11.75)
        gap = copy.deepcopy(SAMPLES)
        gap[1]["monotonic"], gap[-1]["monotonic"] = 12.01, 14.0
        with self.assertRaisesRegex(resources.ResourceObservationError, "resource_sampling_gap"):
            resources.reduce_envelope(PLAN, TABLES, MIGRATION_TABLES, gap, 3.55,
                                      started_monotonic=10.25, completed_monotonic=13.8)

    def test_sample_boundaries_and_duration_must_bracket_operation(self):
        no_before = copy.deepcopy(SAMPLES)
        no_before[0]["boundary"] = "during"
        late_after = copy.deepcopy(SAMPLES)
        late_after[-1]["monotonic"] = 14.0
        for samples in (no_before, late_after):
            with self.subTest(samples=samples), self.assertRaises(resources.ResourceObservationError):
                resources.reduce_envelope(PLAN, TABLES, MIGRATION_TABLES, samples, 1.5,
                                          started_monotonic=10.25, completed_monotonic=11.75)
        with self.assertRaises(resources.ResourceObservationError):
            resources.reduce_envelope(PLAN, TABLES, MIGRATION_TABLES, copy.deepcopy(SAMPLES), 1.6,
                                      started_monotonic=10.25, completed_monotonic=11.75)

    def test_plan_table_and_scope_identity_are_strict_and_ordered(self):
        plan, tables, scopes, plan_hash, table_hash, scope_hash = resources.validate_plan_and_tables(
            PLAN, TABLES, MIGRATION_TABLES)
        self.assertEqual(plan, PLAN)
        self.assertEqual(tables, TABLES)
        self.assertEqual(scopes, MIGRATION_TABLES)
        self.assertEqual(plan_hash, resources.pending_migration_plan_hash(PLAN))
        self.assertEqual(table_hash, resources._hash(TABLES))
        self.assertEqual(scope_hash, resources._hash(MIGRATION_TABLES))
        reversed_plan = list(reversed(PLAN))
        reversed_scope = list(reversed(MIGRATION_TABLES))
        self.assertNotEqual(resources.validate_plan_and_tables(reversed_plan, TABLES, reversed_scope)[3], plan_hash)
        bad_cases = [
            ([PLAN[0], PLAN[0]], TABLES, MIGRATION_TABLES),
            (PLAN, [TABLES[0], TABLES[0]], MIGRATION_TABLES),
            (PLAN, [{**TABLES[0], "reference_rows": 99999}], MIGRATION_TABLES),
            (PLAN, [{**TABLES[0], "reference_bytes": 0}], MIGRATION_TABLES),
            (PLAN, [{**TABLES[0], "table": "public.bad-name"}], MIGRATION_TABLES),
            ([{"app_label": "tracer", "name": "bad/name"}], [], []),
            (PLAN, TABLES, list(reversed(MIGRATION_TABLES))),
            (PLAN, TABLES, [{**MIGRATION_TABLES[0], "tables": []},
                            {**MIGRATION_TABLES[1], "tables": []}]),
        ]
        for pending, refs, scopes in bad_cases:
            with self.subTest(pending=pending, refs=refs, scopes=scopes), self.assertRaises(
                    resources.ResourceObservationError):
                resources.validate_plan_and_tables(pending, refs, scopes)
        for omitted in (None, []):
            with self.subTest(omitted_scope=omitted), self.assertRaises(resources.ResourceObservationError):
                resources.validate_plan_and_tables(PLAN, TABLES, omitted)

    def test_fixed_sampler_brackets_normal_operation_and_binds_private_inputs(self):
        sampler = FixedSampler()
        result = sampler.measure(lambda: "real-start-return")
        self.assertEqual(result.operation_result, "real-start-return")
        self.assertEqual(result.migration_measurement["measurement_basis"], resources.MEASUREMENT_BASIS)
        evidence = result.private_evidence
        self.assertEqual(evidence["snapshot_generation"], "a" * 64)
        self.assertEqual(evidence["target_digest"], "sha256:" + "b" * 64)
        self.assertEqual(evidence["ordered_plan_sha256"], resources.pending_migration_plan_hash(PLAN))
        self.assertEqual(evidence["table_references_sha256"], resources._hash(TABLES))
        self.assertEqual(evidence["migration_table_scope_sha256"], resources._hash(MIGRATION_TABLES))
        self.assertEqual(evidence["migration_tables"], MIGRATION_TABLES)
        self.assertEqual(evidence["measurement"]["migration_tables"], MIGRATION_TABLES)
        self.assertEqual(evidence["target_identity_sha256"], resources._hash(evidence["target_identity"]))
        self.assertEqual(evidence["samples"][0]["boundary"], "before_start")
        self.assertEqual(evidence["samples"][-1]["boundary"], "after_end")
        self.assertTrue(all("processed_bytes" not in row for row in evidence["samples"]))
        self.assertTrue(sampler._stop.is_set())
        with self.assertRaises(resources.ResourceObservationError):
            sampler.measure(lambda: None)

    def test_caller_and_sampler_visible_input_mutation_cannot_change_hash_bound_measurement(self):
        caller_plan = copy.deepcopy(PLAN)
        caller_tables = copy.deepcopy(TABLES)
        caller_scope = copy.deepcopy(MIGRATION_TABLES)
        sampler = FixedSampler(pending_plan=caller_plan, tables=caller_tables,
                               migration_tables=caller_scope)
        caller_plan[0]["name"] = "caller_mutation"
        caller_tables[0]["reference_rows"] = 1
        caller_scope[1]["tables"][0]["table"] = "public.caller_mutation"
        # These are detached views, not the private snapshots used by measure().
        sampler.plan[0]["name"] = "view_mutation"
        sampler.tables[0]["reference_rows"] = 2
        sampler.migration_tables[1]["tables"][0]["table"] = "public.view_mutation"
        result = sampler.measure(lambda: None)
        measured = result.migration_measurement
        evidence = result.private_evidence
        self.assertEqual(measured["pending_plan"], PLAN)
        self.assertEqual(measured["tables"], TABLES)
        self.assertEqual(measured["migration_tables"], MIGRATION_TABLES)
        self.assertEqual(evidence["ordered_plan_sha256"], resources.pending_migration_plan_hash(PLAN))
        self.assertEqual(evidence["table_references_sha256"], resources._hash(TABLES))
        self.assertEqual(evidence["migration_table_scope_sha256"], resources._hash(MIGRATION_TABLES))
        self.assertEqual(evidence["migration_tables"], MIGRATION_TABLES)

    def test_failed_operation_stops_sampler_and_emits_no_measurement(self):
        sampler = FixedSampler(interval_seconds=0.005)
        with self.assertRaisesRegex(ValueError, "bootstrap failed"):
            sampler.measure(lambda: (_ for _ in ()).throw(ValueError("bootstrap failed")))
        self.assertTrue(sampler._stop.is_set())
        self.assertGreaterEqual(sampler.read_count, 1)

    def test_reader_refuses_unbound_host_paths_and_container_ids(self):
        malformed = {
            "schema_version": 2,
            "docker_root": "/tmp/other-daemon",
            "volumes": [],
            "containers": [],
        }
        with self.assertRaises(resources.ResourceObservationError):
            resources._native_reader(malformed)
        with self.assertRaises(resources.ResourceObservationError):
            resources._safe_absolute("/var/lib/docker/../etc")
        with self.assertRaises(resources.ResourceObservationError):
            resources._safe_absolute("relative/path")
        self.assertEqual(resources._memory_for_container({
            "id": "c" * 64, "pid": 0, "status": "created",
        }), 0)
        with self.assertRaises(resources.ResourceObservationError):
            resources._memory_for_container({
                "id": "c" * 64, "pid": 0, "status": "running",
            })

    def test_native_reader_refuses_omitted_owned_roots_and_unreadable_log_parent(self):
        volumes = [{
            "role": role, "name": "phase1_" + role,
            "mountpoint": "/var/lib/docker/volumes/phase1_" + role + "/_data",
            "root_identity": "1:1",
        } for role in resources.VOLUME_ROLES]
        containers = [{
            "service": service, "id": chr(97 + index) * 64, "pid": 0, "status": "created",
            "upper_dir": "/var/lib/docker/overlay2/" + chr(100 + index) * 64 + "/diff",
            "log_driver": "json-file",
            "log_path": "/var/lib/docker/containers/" + chr(97 + index) * 64 + "/"
                       + chr(97 + index) * 64 + "-json.log",
        } for index, service in enumerate(resources.SERVICES)]
        request = {"schema_version": 2, "docker_root": "/var/lib/docker",
                   "volumes": volumes, "containers": containers}
        with self.assertRaises(resources.ResourceObservationError):
            resources._native_reader({**request, "volumes": volumes[:-1]})
        incomplete = copy.deepcopy(request)
        del incomplete["containers"][0]["upper_dir"]
        with self.assertRaises(resources.ResourceObservationError):
            resources._native_reader(incomplete)
        for driver in ('journald','syslog','fluentd'):
            unsupported = copy.deepcopy(request)
            unsupported['containers'][0].update(log_driver=driver,log_path='')
            with self.subTest(driver=driver), patch.object(resources, '_open_dir') as opened, \
                 self.assertRaises(resources.ResourceObservationError):
                resources._native_reader(unsupported)
            opened.assert_not_called()
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
            missing_log = os.path.join(temporary, "missing-container", "container.log")
            with self.assertRaises(OSError):
                resources._owned_log_bytes(missing_log, counter=[0], seen=set())

        class FailedNativeReader:
            def __init__(self):
                self.returncode = None
                self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()

            def communicate(self, _payload, timeout):
                self.returncode = 2
                return b"", b""

            def poll(self):
                return self.returncode

        sampler = FixedSampler()
        with patch.object(resources.subprocess, "Popen", return_value=FailedNativeReader()):
            with self.assertRaisesRegex(resources.ResourceObservationError, "resource_reader_failed"):
                sampler._fixed_reader({})

    def test_unrelated_filesystem_cleanup_cannot_cancel_owned_allocated_growth(self):
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
            owned = Path(temporary) / "owned-volume"
            unrelated = Path(temporary) / "unrelated"
            owned.mkdir()
            unrelated.mkdir()
            with (unrelated / "cleanup-me.bin").open("wb") as stream:
                stream.write(b"u" * (8 * 1024 * 1024))

            def owned_bytes():
                fd = resources._open_dir(str(owned.resolve()))
                try:
                    return resources._owned_allocated_tree(fd, [0], set())
                finally:
                    os.close(fd)

            before = owned_bytes()
            with (owned / "target-growth.bin").open("wb") as stream:
                stream.write(b"t" * (1024 * 1024))
            (unrelated / "cleanup-me.bin").unlink()
            after = owned_bytes()
            self.assertGreater(after, before)
            samples = [
                {"monotonic": 1.0, "boundary": "before_start", "disk_bytes": before,
                 "wal_bytes": 0, "temp_bytes": 0, "memory_bytes": 0},
                {"monotonic": 2.0, "boundary": "after_end", "disk_bytes": after,
                 "wal_bytes": 0, "temp_bytes": 0, "memory_bytes": 0},
            ]
            measured = resources.reduce_envelope([], [], [], samples, 0.5,
                started_monotonic=1.25, completed_monotonic=1.75)
            self.assertEqual(measured["peak_disk_growth_bytes"], after - before)

    def test_empty_wal_and_temp_are_measured_separately_from_missing_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = os.path.join(temporary, "postgres")
            os.makedirs(os.path.join(root, "pg_wal"))
            os.makedirs(os.path.join(root, "base", "16384", "pgsql_tmp"))
            os.makedirs(os.path.join(root, "pg_tblspc"))
            root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                self.assertEqual(resources._postgres_resource_bytes(root_fd, [0]), (0, 0))
                wal_file = os.path.join(root, "pg_wal", "000000010000000000000001")
                temp_file = os.path.join(root, "base", "16384", "pgsql_tmp", "pgsql_tmp1.0")
                with open(wal_file, "wb") as stream:
                    stream.write(b"w" * 4096)
                with open(temp_file, "wb") as stream:
                    stream.write(b"t" * 4096)
                wal_bytes, temp_bytes = resources._postgres_resource_bytes(root_fd, [0])
                self.assertGreater(wal_bytes, 0)
                self.assertGreater(temp_bytes, 0)
                os.unlink(os.path.join(root, "pg_wal", "000000010000000000000001"))
                os.unlink(temp_file)
                os.rmdir(os.path.join(root, "pg_wal"))
                with self.assertRaises(OSError):
                    resources._postgres_resource_bytes(root_fd, [0])
            finally:
                os.close(root_fd)

    def test_native_sampler_refuses_local_docker_before_any_inspection(self):
        with patch.object(resources, "_native_amd64_actions", return_value=False):
            with self.assertRaisesRegex(resources.ResourceObservationError, "native_amd64_runner_required"):
                resources.NormalBootstrapEnvelopeSampler(None, binding={}, roster={}, target_image={},
                    snapshot_generation="a" * 64, pending_plan=[], tables=[], migration_tables=[])

    def test_counter_sources_are_explicit_and_do_not_claim_rss_or_between_sample_guarantees(self):
        sources = resources.counter_sources()
        self.assertEqual(sources["disk_bytes"]["unit"], "bytes")
        self.assertIn("st_blocks", sources["disk_bytes"]["source"])
        self.assertIn("unrelated filesystem activity is excluded", sources["disk_bytes"]["source"])
        self.assertIn("memory.current", sources["memory_bytes"]["source"])
        self.assertIn("not RSS", sources["memory_bytes"]["source"])
        self.assertFalse(sources["sampling"]["spikes_between_samples_measured"])
        serialized = str(sources).lower()
        self.assertNotIn("password", serialized)
        self.assertNotIn("wal contents", serialized)


if __name__ == "__main__":
    unittest.main()
