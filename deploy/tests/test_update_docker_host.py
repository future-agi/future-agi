"""Synthetic adapter contracts plus real stdlib subprocess bounds; no Docker.

The private qualification report separately records actual dedicated daemon
measurements. These fixtures do not qualify any image, platform or release.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.update import docker_host as host  # noqa: E402
from deploy.update.orchestrator import GuardRefused  # noqa: E402

IMAGE = "sha256:" + "a" * 64
INFO = {"ID": "daemon-a", "OSType": "linux", "Architecture": "aarch64", "Driver": "overlayfs",
        "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]], "DockerRootDir": "/var/lib/docker",
        "Containerd": {"Address": "/run/containerd/containerd.sock"}}
ROW = {"device": "8:1", "filesystem": "ext4", "backing_sha256": "b" * 64, "available_bytes": 123456789}
DOCUMENT = {"schema_version": 1, "backend": "external-containerd-overlayfs",
            "stores": {"content": ROW, "snapshots": dict(ROW, available_bytes=123456700)}}


class SyntheticHost:
    def __init__(self):
        self.environ = {"DOCKER_CONTEXT": "fixture"}
        self.root = ROOT
        self.docker = ["synthetic-docker"]
        self.info = copy.deepcopy(INFO)
        self.image = {"Id": IMAGE, "Os": "linux"}
        self.document = json.dumps(DOCUMENT)
        self.calls = []
        self.daemon_reads = 0
        self.swap = False
        self.returncode = 0
        self.helper_row = None
        self.healthcheck = {"Test": ["NONE"]}

    def subprocess_env(self):
        return dict(self.environ)

    def docker_run(self, *args, **kwargs):
        self.calls.append(list(args))
        if args[0] == "info":
            self.daemon_reads += 1
            value = dict(self.info)
            if self.swap and self.daemon_reads >= 2:
                value["ID"] = "daemon-b"
        elif args[:2] == ("image", "inspect"):
            value = [self.image]
        elif args[0] == "inspect":
            value = [self.helper_row]
        else:
            value = {}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    @staticmethod
    def mount(spec):
        # What docker inspect reports for a requested read-only bind.
        fields = dict(item.split("=", 1) for item in spec.split(",") if "=" in item)
        return {"Type": fields["type"], "Source": fields["src"], "Destination": fields["dst"],
                "RW": "readonly" not in spec.split(",")}

    def runner(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[1] == "create":
            self.helper_row = {"Id": "c" * 64, "Name": "/" + args[args.index("--name") + 1], "Image": IMAGE,
                "Config": {"Healthcheck": self.healthcheck, "Entrypoint": ["/usr/local/bin/python3"],
                           "Cmd": ["-I", "-B", "-S", "-"]},
                "HostConfig": {"Tmpfs": {args[i+1].split(':')[0]: args[i+1].split(':',1)[1]
                    for i, arg in enumerate(args) if arg == '--tmpfs'}},
                "Mounts": [self.mount(args[i+1]) for i, arg in enumerate(args) if arg == "--mount"],
                "State": {"Status": "created", "Running": False}}
            return subprocess.CompletedProcess(args, self.returncode, "c" * 64 + "\n", "")
        if args[1] == "start":
            return subprocess.CompletedProcess(args, self.returncode, self.document, "")
        raise AssertionError(args)


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.inst = SyntheticHost()
        self.registration = SimpleNamespace(binding=SimpleNamespace(docker_context="fixture", daemon_identity="daemon-a"))
        self.clone = patch.object(host, "bounded_installation", side_effect=lambda inst, timeout=60: inst)
        self.clone.start()
        self.addCleanup(self.clone.stop)

    def inspect(self):
        return host.inspect_daemon_storage(self.inst, self.registration, helper_image_id=IMAGE)

    def test_actual_callback_value_is_minimum_of_two_proven_stores(self):
        result = self.inspect()
        self.assertEqual((result.status, result.available_bytes), ("available", 123456700))
        self.assertEqual(result.evidence_sha256, hashlib.sha256(self.inst.document.encode()).hexdigest())
        self.assertEqual(host.observe_daemon_storage(self.inst, self.registration, helper_image_id=IMAGE), 123456700)

    def test_fixed_safe_mounts_exact_local_image_and_no_network_socket_or_pull(self):
        self.inspect()
        calls = [row for row in self.inst.calls if isinstance(row, tuple)]
        self.assertEqual(len(calls), 2)
        argv, kwargs = calls[0]
        self.assertEqual(argv[1], "create")
        self.assertIn("--no-healthcheck", argv)
        self.assertEqual(calls[1][0][1:4], ["start", "-a", "-i"])
        self.assertEqual(calls[1][0][-1], "c" * 64)
        self.assertIn(IMAGE, argv)
        self.assertEqual(argv[argv.index("--pull") + 1], "never")
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
        mounts = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--mount"]
        self.assertEqual(len(mounts), 4)
        self.assertTrue(all(",readonly," in mount for mount in mounts))
        self.assertFalse(any("sock" in mount for mount in mounts))
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("input", kwargs)
        self.assertEqual(calls[1][1]["input"], host.fixed_source("daemon_storage.py"))
        cleanup = [row for row in self.inst.calls if isinstance(row, list) and row[:2] == ["rm", "-f"]]
        self.assertEqual(cleanup[0][2], argv[argv.index("--name") + 1])

    def test_daemon_helper_masks_inherited_image_volumes(self):
        self.inst.image['Config'] = {'Volumes': {'/data': {}, '/cache': {}}}
        result = self.inspect()
        self.assertEqual(result.status, 'available')
        create = next(row[0] for row in self.inst.calls if isinstance(row, tuple) and row[0][1] == 'create')
        self.assertEqual({create[i+1] for i, arg in enumerate(create) if arg == '--tmpfs'},
            {'/data:rw,noexec,nosuid,nodev,size=1m', '/cache:rw,noexec,nosuid,nodev,size=1m'})
        self.assertTrue(any(isinstance(row, list) and row[:2] == ['rm', '-f'] for row in self.inst.calls))

    def test_daemon_helper_with_inherited_healthcheck_is_never_started(self):
        for value in (None, {}, {"Test": ["CMD", "release-code"]}):
            with self.subTest(healthcheck=value):
                self.inst.healthcheck = value
                self.inst.calls.clear()
                self.assertEqual(self.inspect().status, "unknown")
                self.assertFalse(any(isinstance(c, tuple) and c[0][1] == "start" for c in self.inst.calls))

    def test_no_preprovisioned_helper_is_unsupported_not_constant_success(self):
        result = host.inspect_daemon_storage(self.inst, self.registration)
        self.assertEqual((result.status, result.available_bytes), ("unsupported", None))
        self.assertFalse(any(isinstance(row, tuple) for row in self.inst.calls))

    def test_unknown_backend_is_unsupported_without_running_helper(self):
        self.inst.info["Driver"] = "devicemapper"
        self.assertEqual(self.inspect().status, "unsupported")
        self.assertFalse(any(isinstance(row, tuple) for row in self.inst.calls))

    def test_daemon_swap_or_context_override_fails_closed(self):
        self.inst.swap = True
        self.assertIsNone(self.inspect().available_bytes)
        self.inst.swap = False
        self.inst.environ["DOCKER_HOST"] = "tcp://wrong:2375"
        self.assertIsNone(self.inspect().available_bytes)

    def test_wrong_local_image_id_cannot_run(self):
        self.inst.image["Id"] = "sha256:" + "f" * 64
        self.assertIsNone(self.inspect().available_bytes)
        self.assertFalse(any(isinstance(row, tuple) for row in self.inst.calls))

    def test_missing_malformed_duplicate_oversized_or_bool_capacity_refused(self):
        bad = copy.deepcopy(DOCUMENT)
        bad["stores"]["content"]["available_bytes"] = True
        missing = copy.deepcopy(DOCUMENT)
        del missing["stores"]["snapshots"]
        for value in ("{}", "not json", '{"x":1,"x":2}', " " * 5000, json.dumps(bad), json.dumps(missing),
                      json.dumps(dict(DOCUMENT, schema_version=True)), json.dumps(dict(DOCUMENT, stores=["content", "snapshots"]))):
            with self.subTest(value=value[:30]):
                self.inst.document = value
                self.assertIsNone(self.inspect().available_bytes)

    def test_helper_failure_and_exception_do_not_leak_output(self):
        self.inst.returncode = 2
        self.inst.document = "secret password from error"
        result = self.inspect()
        self.assertIsNone(result.available_bytes)
        self.assertNotIn("secret", repr(result))
        self.inst.runner = lambda *a, **k: (_ for _ in ()).throw(host.ObservationFailure("timeout"))
        self.assertIsNone(self.inspect().available_bytes)

    def test_native_adapter_reused_with_pre_post_id_checks(self):
        self.inst.info["Driver"] = "overlay2"
        with patch.object(host, "daemon_space_available", return_value=98765) as native:
            self.assertEqual(self.inspect().available_bytes, 98765)
            native.assert_called_once_with(self.inst, self.registration.binding)
        self.inst.swap = True
        with patch.object(host, "daemon_space_available", return_value=98765):
            self.assertIsNone(self.inspect().available_bytes)

    def test_native_unknown_without_helper_is_never_client_disk_fallback(self):
        self.inst.info["Driver"] = "overlay2"
        with patch.object(host, "daemon_space_available", return_value=None):
            result = host.inspect_daemon_storage(self.inst, self.registration)
        self.assertEqual((result.status, result.backend), ("unsupported", "native-overlay2"))
        self.assertFalse(any(isinstance(row, tuple) for row in self.inst.calls))

    def overlay2_helper(self):
        self.inst.info.update(Driver="overlay2", DriverStatus=[["Backing Filesystem", "extfs"]])
        self.inst.document = json.dumps({"schema_version": 1, "backend": "helper-overlay2",
                                         "stores": {"overlay2": dict(ROW, available_bytes=4242)}})

    def test_unprivileged_overlay2_is_measured_by_the_fixed_daemon_side_probe(self):
        # A non-root installation user cannot read dockerd's ns/mnt or /var/lib/docker.
        self.overlay2_helper()
        with patch.object(host, "daemon_space_available", return_value=None):
            result = self.inspect()
        self.assertEqual((result.status, result.available_bytes, result.backend), ("available", 4242, "helper-overlay2"))
        self.assertEqual([s.role for s in result.stores], ["overlay2"])
        create, start = [row for row in self.inst.calls if isinstance(row, tuple)]
        mounts = [create[0][i+1] for i, arg in enumerate(create[0]) if arg == "--mount"]
        self.assertEqual(mounts, [
            "type=bind,src=/proc,dst=/host-proc,readonly,bind-propagation=rprivate",
            "type=bind,src=/var/lib/docker/overlay2,dst=/daemon-store,readonly,bind-propagation=rslave,bind-recursive=disabled"])
        # Native Docker 28: "must use either propagation mode rslave or rshared when mount source is within
        # the daemon root". The daemon-root store bind never uses rprivate.
        for flag, value in (("--network", "none"), ("--cap-drop", "ALL"), ("--pull", "never")):
            self.assertEqual(create[0][create[0].index(flag) + 1], value)
        self.assertNotIn("--privileged", create[0])
        self.assertEqual(start[1]["input"], host.fixed_source("daemon_overlay2.py"))
        self.assertTrue(any(isinstance(row, list) and row[:2] == ["rm", "-f"] for row in self.inst.calls))

    def test_overlay2_helper_refuses_other_roots_snapshotters_documents_and_mounts(self):
        cases = [
            ("root", lambda: self.inst.info.update(DockerRootDir="/srv/docker")),
            ("snapshotter", lambda: self.inst.info.update(DriverStatus=[["driver-type", "io.containerd.snapshotter.v1"]])),
            ("no helper", None),
        ]
        for label, change in cases:
            with self.subTest(label=label):
                self.setUp(); self.overlay2_helper()
                if change: change()
                with patch.object(host, "daemon_space_available", return_value=None):
                    result = self.inspect() if label != "no helper" else host.inspect_daemon_storage(self.inst, self.registration)
                self.assertEqual(result.status, "unsupported")
                self.assertFalse(any(isinstance(row, tuple) for row in self.inst.calls))
        for document in ({"schema_version": 1, "backend": "external-containerd-overlayfs",
                          "stores": {"overlay2": ROW}},
                         {"schema_version": 1, "backend": "helper-overlay2",
                          "stores": {"content": ROW, "snapshots": ROW}}):
            with self.subTest(document=document["backend"]):
                self.setUp(); self.overlay2_helper(); self.inst.document = json.dumps(document)
                with patch.object(host, "daemon_space_available", return_value=None):
                    self.assertEqual(self.inspect().status, "unknown")
        # A bind that is writable or from another source is never started.
        self.setUp(); self.overlay2_helper()
        self.inst.mount = lambda spec: dict(SyntheticHost.mount(spec), RW=True)
        with patch.object(host, "daemon_space_available", return_value=None):
            self.assertEqual(self.inspect().status, "unknown")
        self.assertFalse(any(isinstance(c, tuple) and c[0][1] == "start" for c in self.inst.calls))


class Overlay2ProbeTests(unittest.TestCase):
    """Run the pinned probe's own logic against a synthetic /proc and store."""

    def run_probe(self, *, argv=("/usr/bin/dockerd", "-H", "fd://"), device=None, filesystem="ext4",
                  readonly=True, dockerds=1, mounted_at="/var/lib/docker"):
        import os, tempfile
        source = host.fixed_source("daemon_overlay2.py").decode()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            proc, store = base / "proc", base / "store"
            store.mkdir()
            info = store.stat()
            actual = "{}:{}".format(os.major(info.st_dev), os.minor(info.st_dev))
            for pid in range(100, 100 + dockerds):
                d = proc / str(pid); d.mkdir(parents=True)
                (d / "comm").write_bytes(b"dockerd\n")
                (d / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
                (d / "mountinfo").write_text(
                    "1 0 0:1 / / rw - overlay overlay rw\n"
                    "2 1 {} / {} rw,relatime - {} /dev/root rw\n".format(device or actual, mounted_at, filesystem))
            (proc / "self").mkdir(parents=True)  # non-numeric entries are skipped
            namespace = {"__name__": "probe"}
            exec(compile(source, "daemon_overlay2.py", "exec"), namespace)
            real = namespace["Path"]
            namespace["Path"] = lambda value: (proc if value == "/host-proc" else store if value == "/daemon-store"
                                               else real(value))
            flag = os.ST_RDONLY if readonly else 0
            statvfs = lambda path: SimpleNamespace(f_flag=flag, f_bavail=10, f_frsize=4096)
            namespace["os"] = SimpleNamespace(**{k: getattr(os, k) for k in ("major", "minor", "ST_RDONLY")},
                                              statvfs=statvfs)
            return namespace["observe"](), actual

    def test_measures_the_store_on_the_device_dockerd_mounts_at_its_data_root(self):
        document, device = self.run_probe()
        self.assertEqual(document["backend"], "helper-overlay2")
        self.assertEqual(set(document["stores"]), {"overlay2"})
        row = document["stores"]["overlay2"]
        self.assertEqual((row["device"], row["filesystem"], row["available_bytes"]), (device, "ext4", 40960))

    def test_mismatch_or_ambiguity_refuses(self):
        cases = {"other device": dict(device="254:99"), "unsupported filesystem": dict(filesystem="tmpfs"),
                 "writable": dict(readonly=False), "two dockerds": dict(dockerds=2), "no dockerd": dict(dockerds=0),
                 "other data root": dict(argv=("/usr/bin/dockerd", "--data-root", "/srv/docker")),
                 "legacy graph flag": dict(argv=("/usr/bin/dockerd", "-g", "/srv/docker"))}
        for label, change in cases.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.run_probe(**change)


class RunnerTests(unittest.TestCase):
    def test_bounded_stdin_stdout_roundtrip(self):
        runner = host.ReadOnlyRunner(5)
        result = runner([sys.executable, "-I", "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                        input=b"x" * 200000, text=False)
        self.assertEqual(result.stdout, b"x" * 200000)

    def test_real_oversized_stdout_and_stderr_refused(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), self.assertRaises(host.ObservationFailure) as error:
                host.ReadOnlyRunner(5, max_output=1024)([sys.executable, "-I", "-c",
                    "import sys;sys." + stream + ".write('secret'*100000)"])
            self.assertEqual(str(error.exception), "output_limit")

    def test_real_deadline_kills_waiting_process(self):
        with self.assertRaises(host.ObservationFailure) as error:
            host.ReadOnlyRunner(.15)([sys.executable, "-I", "-c", "import time;time.sleep(10)"])
        self.assertEqual(str(error.exception), "timeout")

    def test_stdin_not_consumed_does_not_block_deadline(self):
        with self.assertRaises(host.ObservationFailure):
            host.ReadOnlyRunner(.15)([sys.executable, "-I", "-c", "import time;time.sleep(10)"], input=b"a" * 2000000)

    def test_source_digest_is_checked_before_execution(self):
        source = host.fixed_source("daemon_storage.py")
        self.assertEqual(hashlib.sha256(source).hexdigest(), host.SOURCE_DIGESTS["daemon_storage.py"])
        with patch.dict(host.SOURCE_DIGESTS, {"daemon_storage.py": "0" * 64}):
            with self.assertRaises(host.ObservationFailure):
                host.fixed_source("daemon_storage.py")
        with self.assertRaises(GuardRefused):
            host.fixed_source("../../web-input.py")


class HelperLifecycleTests(unittest.TestCase):
    def fixture(self):
        inst = SyntheticHost()
        inst.image.update(Config={"Volumes": {"/data": {}}})
        inst.env, inst.env_overlay = {}, {}
        inst.row, inst.payload, inst.calls = None, b"", []
        inst.bad = False
        inst.missing_cleanup = False
        inst.timeout = False
        def runner(args, **kw):
            inst.calls.append((args, kw))
            if args[1] == "create":
                name = args[args.index("--name") + 1]
                token = args[args.index("--label") + 1].split("=", 1)[1]
                inst.row = {"Id": "c" * 64, "Name": "/" + name, "Image": IMAGE,
                    "Config": {"Labels": {host.HELPER_LABEL: token}, "Entrypoint": ["/usr/local/bin/python3"],
                               "Healthcheck": {"Test": ["NONE"]} if "--no-healthcheck" in args else None,
                               "Cmd": ["-I", "-B", "-S", "-", "sizes"]},
                    "HostConfig": {"ReadonlyRootfs": not inst.bad, "NetworkMode": "none", "RestartPolicy": {"Name": "no"},
                        "Privileged": False, "CapDrop": ["ALL"], "CapAdd": ["DAC_READ_SEARCH"], "Memory": 256 * 1024 ** 2,
                        "PidsLimit": 32, "NanoCpus": 500000000, "SecurityOpt": ["no-new-privileges"], "Tmpfs": {"/data": "masked"}},
                    "Mounts": [{"Type": "volume", "Name": "fixture-owned", "Destination": "/observed-volume", "RW": False}],
                    "State": {"Running": False, "Status": "created"}}
                return subprocess.CompletedProcess(args, 0, "c" * 64 + "\n", "")
            if args[1] == "start":
                inst.payload = kw["input"]
                if inst.timeout: raise host.ObservationFailure("timeout")
                inst.row["State"].update(Status="exited", ExitCode=0)
                return subprocess.CompletedProcess(args, 0, '{"ok":true}', "")
            raise AssertionError(args)
        def docker_run(*args, **kw):
            inst.calls.append(list(args))
            if args[0] == "info": value = inst.info
            elif args[:2] == ("image", "inspect"): value = [inst.image]
            elif args[0] == "inspect": value = [inst.row]
            elif args[0] == "ps":
                return subprocess.CompletedProcess(args, 0, "c" * 64 if inst.row is not None else "", "")
            elif args[:2] == ("rm", "-f"):
                if not inst.missing_cleanup: inst.row = None
                return subprocess.CompletedProcess(args, 0, "removed", "")
            else: raise AssertionError(args)
            return subprocess.CompletedProcess(args, 0, json.dumps(value), "")
        inst.runner, inst.docker_run = runner, docker_run
        return inst

    def invoke(self, inst):
        binding = SimpleNamespace(docker_context="fixture", daemon_identity="daemon-a")
        with patch.object(host, "bounded_installation", side_effect=lambda obj, *a, **k: obj):
            return host.run_readonly_helper(inst, binding, image_id=IMAGE, source=b"fixed-source-only",
                volume_name="fixture-owned", arguments=("sizes",))

    def test_create_inspect_start_by_exact_id_no_anonymous_volume_and_verified_cleanup(self):
        inst = self.fixture()
        self.assertEqual(self.invoke(inst), b'{"ok":true}')
        self.assertIsNone(inst.row)
        commands = [c[0] for c in inst.calls if isinstance(c, tuple)]
        self.assertEqual(commands[1][-1], "c" * 64)
        self.assertEqual(commands[1][1:4], ["start", "-a", "-i"])
        self.assertIn("--tmpfs", commands[0])
        self.assertNotIn("--rm", commands[0])
        self.assertEqual(inst.payload, b"fixed-source-only")
        self.assertTrue(commands[0][commands[0].index("--name") + 1].startswith("fagi-upd-e2e-observe-"))

    def test_fixed_helper_disables_inherited_healthcheck_before_start(self):
        inst = self.fixture()
        inst.image["Config"]["Healthcheck"] = {"Test": ["CMD-SHELL", "release-defined-code"]}
        original = inst.runner
        def runner(args, **kwargs):
            result = original(args, **kwargs)
            if args[1] == "create":
                self.assertIn("--no-healthcheck", args)
                inst.row["Config"]["Healthcheck"] = {"Test": ["NONE"]}
            return result
        inst.runner = runner
        self.assertEqual(self.invoke(inst), b'{"ok":true}')

    def test_missing_or_executable_healthcheck_is_refused_before_start(self):
        for healthcheck in (None, {}, {"Test": ["CMD-SHELL", "release-defined-code"]}, {"Test": ["CMD", "python", "payload"]}):
            with self.subTest(healthcheck=healthcheck):
                inst = self.fixture()
                original = inst.runner
                def runner(args, **kwargs):
                    result = original(args, **kwargs)
                    if args[1] == "create":
                        inst.row["Config"]["Healthcheck"] = healthcheck
                    return result
                inst.runner = runner
                with self.assertRaises(GuardRefused):
                    self.invoke(inst)
                self.assertFalse(any(isinstance(c, tuple) and c[0][1] == "start" for c in inst.calls))
                self.assertIsNone(inst.row)

    def test_unverified_readwrite_helper_never_started(self):
        inst = self.fixture()
        inst.bad = True
        with self.assertRaises(GuardRefused): self.invoke(inst)
        self.assertFalse(any(isinstance(c, tuple) and c[0][1] == "start" for c in inst.calls))
        self.assertIsNone(inst.row)

    def test_timeout_still_removes_exact_helper(self):
        inst = self.fixture()
        inst.timeout = True
        with self.assertRaises(host.ObservationFailure): self.invoke(inst)
        self.assertIsNone(inst.row)
        self.assertIn(["rm", "-f", "c" * 64], inst.calls)

    def test_failed_cleanup_cannot_return_a_positive_observation(self):
        inst = self.fixture()
        inst.missing_cleanup = True
        with self.assertRaises(GuardRefused): self.invoke(inst)


class VolumeCapacityTests(unittest.TestCase):
    def setUp(self):
        from deploy.tests.test_update_standalone_state import RuntimeFixture
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.inst = RuntimeFixture(Path(self.temp.name).resolve())
        self.registration = SimpleNamespace(binding=self.inst.binding)
        self.clone = patch.object(host, "bounded_installation", side_effect=lambda inst, timeout=60: inst)
        self.clone.start()
        self.addCleanup(self.clone.stop)
        self.row = {"schema_version": 2, "root_identity": "2049:123", "device": "8:1", "filesystem": "ext4",
                    "backing_sha256": "e" * 64, "free_bytes": 123456, "free_inodes": 999,
                    "used_bytes": 8192, "logical_bytes": 8 * 1024 ** 3, "members": 2}
        self.helper = patch.object(host, "run_readonly_helper", return_value=json.dumps(self.row).encode())
        self.mock_helper = self.helper.start()
        self.addCleanup(self.helper.stop)

    def inspect(self, **kwargs):
        return host.inspect_volume_storage(self.inst, self.registration, helper_image_id=IMAGE, **kwargs)

    def test_every_named_volume_has_usage_bytes_inodes_and_backing(self):
        result = self.inspect()
        self.assertEqual(result.status, "available")
        self.assertEqual({r.role for r in result.volumes}, set(host.VOLUME_ROLES))
        self.assertTrue(all(r.free_inodes == 999 and r.free_bytes == 123456 and r.allocated_bytes == 8192 for r in result.volumes))
        self.assertTrue(all(r.logical_bytes == 8 * 1024 ** 3 and r.members == 2 for r in result.volumes))
        self.assertTrue(all(r.restore_work_bytes == 8 * 1024 ** 3 + 2 * 8192 for r in result.volumes))
        self.assertEqual(self.mock_helper.call_count, 3)
        self.assertTrue(all(call.kwargs["arguments"] == ("sizes",) for call in self.mock_helper.call_args_list))

    def test_identity_does_not_require_an_expensive_tree_walk(self):
        self.mock_helper.return_value = json.dumps(dict(self.row, used_bytes=None, logical_bytes=None, members=None)).encode()
        result = self.inspect(measure_used_bytes=False)
        self.assertEqual(result.status, "available")
        self.assertTrue(all(r.used_bytes is None and r.logical_bytes is None and r.members is None for r in result.volumes))
        with self.assertRaises(GuardRefused):
            _ = result.volumes[0].restore_work_bytes
        self.assertTrue(all(call.kwargs["arguments"] == ("identity",) for call in self.mock_helper.call_args_list))

    def test_capacity_changes_do_not_change_storage_identity(self):
        before = self.inspect().storage_sha256
        self.mock_helper.return_value = json.dumps(dict(self.row, used_bytes=1, free_bytes=2, free_inodes=3)).encode()
        self.assertEqual(before, self.inspect().storage_sha256)
        self.mock_helper.return_value = json.dumps(dict(self.row, root_identity="2049:456")).encode()
        self.assertNotEqual(before, self.inspect().storage_sha256)

    def test_unknown_malformed_negative_boolean_and_missing_are_not_zero(self):
        for key, value in (("free_bytes", True), ("free_inodes", -1), ("used_bytes", None),
                           ("used_bytes", 1.5), ("device", "bad"), ("root_identity", "bad"),
                           ("logical_bytes", None), ("logical_bytes", -1), ("logical_bytes", True),
                           ("logical_bytes", host.MAX_MEASURED_BYTES), ("members", 0), ("members", True),
                           ("members", 1000001), ("members", None),
                           ("schema_version", True), ("schema_version", 1), ("backing_sha256", "f" * 64 + "\n")):
            self.mock_helper.return_value = json.dumps(dict(self.row, **{key: value})).encode()
            with self.subTest(key=key, value=value): self.assertEqual(self.inspect().status, "unknown")
        self.mock_helper.return_value = b"{}"
        self.assertEqual(self.inspect().status, "unknown")

    def test_foreign_consumer_and_external_volume_refuse_before_helper(self):
        self.inst.foreign = [{"Id": "f" * 64, "Mounts": [{"Name": "fixture-app-data"}]}]
        self.assertEqual(self.inspect().status, "unknown")
        self.mock_helper.assert_not_called()
        self.inst.foreign = []
        self.inst.model["volumes"]["app-data"]["external"] = True
        self.assertEqual(self.inspect().status, "unknown")
        self.mock_helper.assert_not_called()

    def test_helper_error_is_unknown_not_capacity_from_client(self):
        self.mock_helper.side_effect = host.ObservationFailure("timeout")
        self.assertEqual(self.inspect().status, "unknown")

    def test_backup_capacity_reads_only_the_selected_directory(self):
        # Real host statvfs validates the BACKUP destination, never daemon space.
        directory = Path(self.temp.name).resolve()
        result = host.inspect_backup_storage(directory)
        self.assertGreater(result["free_bytes"], 0)
        self.assertGreaterEqual(result["free_inodes"], 0)
        link = directory / "link"
        link.symlink_to(directory, target_is_directory=True)
        with self.assertRaises(RuntimeError): host.inspect_backup_storage(link)


class VolumeProbeTests(unittest.TestCase):
    def module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("volume_probe", ROOT / "deploy/update/probes/volume_storage.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_pinned_source_and_isolated_readonly_entrypoint(self):
        raw = host.fixed_source("volume_storage.py")
        self.assertEqual(hashlib.sha256(raw).hexdigest(), host.SOURCE_DIGESTS["volume_storage.py"])
        self.assertIn(b"O_NOFOLLOW", raw)
        self.assertNotIn(b"subprocess", raw)
        self.assertNotIn(b"os.system", raw)

    def test_sparse_logical_workload_and_all_members_are_measured_separately(self):
        import os
        import tempfile
        probe = self.module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with (path / "sparse").open("wb") as stream:
                stream.truncate(8 * 1024 ** 3)
            os.link(path / "sparse", path / "hardlink")
            (path / "escape").symlink_to("/etc")
            (path / "empty-directory").mkdir()
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                measured = probe.tree_sizes(fd)
            finally:
                os.close(fd)
            self.assertEqual(measured["logical_bytes"], 8 * 1024 ** 3)
            self.assertEqual(measured["members"], 5)  # root + both names + link + directory
            expected = sum(p.lstat().st_blocks * 512 for p in (path, path / "sparse", path / "escape", path / "empty-directory"))
            self.assertEqual(measured["used_bytes"], expected)
            self.assertLess(measured["used_bytes"], measured["logical_bytes"])

    def test_allocated_bytes_hardlinks_deduplicated_symlinks_not_followed(self):
        import os
        import tempfile
        probe = self.module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "file").write_bytes(b"x" * 8192)
            os.link(path / "file", path / "hardlink")
            (path / "escape").symlink_to("/etc")
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                expected = sum(p.lstat().st_blocks * 512 for p in (path, path / "file", path / "escape"))
                self.assertEqual(probe.used_bytes(fd), expected)
                with patch.object(probe, "MAX_MEMBERS", 1), self.assertRaises(ValueError): probe.used_bytes(fd)
            finally: os.close(fd)


if __name__ == "__main__":
    unittest.main()


class CheckedDaemonRoutingTests(unittest.TestCase):
    """The real guard, not a mock: every production installation pins the
    registered context as `docker --context <ctx>` (setup, runner, CLI)."""

    CONTEXT = "colima-fagi-update"

    def installation(self, docker, environ=None):
        class Fixture:
            def __init__(self):
                self.docker, self.environ = docker, dict(environ or {})

            def subprocess_env(self):
                return dict(self.environ)

            def docker_run(self, *args, check=False):
                return SimpleNamespace(returncode=0, stdout=json.dumps({"ID": "daemon-1", "Driver": "overlay2"}))
        return Fixture()

    def check(self, docker, environ=None, daemon="daemon-1"):
        binding = SimpleNamespace(docker_context=self.CONTEXT, daemon_identity=daemon)
        return host.checked_daemon(self.installation(docker, environ), binding)

    def test_both_pinned_routings_reach_the_registered_daemon(self):
        for docker, environ in ((["docker", "--context", self.CONTEXT], {}),
                                (["docker", "--context", self.CONTEXT], {"DOCKER_CONTEXT": self.CONTEXT}),
                                (["docker"], {"DOCKER_CONTEXT": self.CONTEXT})):
            with self.subTest(docker=docker, environ=environ):
                self.assertEqual(self.check(docker, environ)["ID"], "daemon-1")

    def test_any_other_routing_is_refused(self):
        for docker, environ in ((["docker", "--context", "other"], {}),
                                (["docker", "--context", self.CONTEXT], {"DOCKER_CONTEXT": "other"}),
                                (["docker"], {}),
                                (["docker"], {"DOCKER_CONTEXT": "other"}),
                                (["docker", "--context", self.CONTEXT, "--debug"], {}),
                                (["docker", "-H", "tcp://elsewhere:2375"], {}),
                                (["docker", "--context", self.CONTEXT], {"DOCKER_HOST": "tcp://elsewhere:2375"}),
                                (["docker"], {"DOCKER_CONTEXT": self.CONTEXT, "DOCKER_TLS_VERIFY": "1"})):
            with self.subTest(docker=docker, environ=environ):
                with self.assertRaises(GuardRefused):
                    self.check(docker, environ)

    def test_a_different_daemon_behind_the_same_context_is_refused(self):
        with self.assertRaises(GuardRefused):
            self.check(["docker", "--context", self.CONTEXT], daemon="another-daemon")
