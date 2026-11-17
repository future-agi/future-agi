"""Actual converter and normalizer bodies with synthetic IO; never opens sockets."""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
STORAGE = ROOT / "simulate/temporal/utils/async_storage.py"
ADMISSION_MODULE = "simulate.temporal.utils.rehost_admission"


def functions(path, ns, names=None):
    tree = ast.parse(path.read_text())
    tree.body = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (names is None or n.name in names)
    ]
    exec(
        compile(
            tree,
            str(path),
            "exec",
            flags=__import__("__future__").annotations.compiler_flag,
        ),
        ns,
    )
    return ns


class Refused(RuntimeError):
    error_code = "BUDGET_PAUSED"


def immediate(coro):
    # All awaits use synthetic immediate collaborators; no asyncio loop/socketpair.
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coro.close()
    raise AssertionError("unexpected real async operation")


class RehostControls(unittest.TestCase):
    def setUp(self):
        self.check = Mock()
        self.download = Mock(return_value=b"synthetic-audio")
        self.upload = Mock(return_value="synthetic-stored")
        self.existing = Mock(return_value=None)
        response = Mock()
        response.iter_content.return_value = [b"synthetic-audio"]
        self.request = Mock(return_value=response)
        self.service = SimpleNamespace(
            is_authenticated_download=Mock(return_value=False),
            is_fagi_s3_url=Mock(return_value=False),
            download_artifact_sync=self.download,
        )

        async def run_in_executor(executor, fn, *args):
            return fn(*args)

        async def async_download(*args, **kwargs):
            return self.download()

        self.modules = patch.dict(
            sys.modules,
            {
                ADMISSION_MODULE: SimpleNamespace(
                    require_recording_rehost_admission=self.check
                ),
                "tracer.utils.vapi_recording": SimpleNamespace(
                    VapiRecordingService=self.service
                ),
                "tfc.utils.storage": SimpleNamespace(upload_audio_to_s3=self.upload),
                "tfc.utils.ssrf_guard": SimpleNamespace(assert_url_host_public=Mock()),
                "asyncio": SimpleNamespace(
                    get_running_loop=lambda: SimpleNamespace(
                        run_in_executor=run_in_executor
                    )
                ),
            },
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.ns = functions(
            STORAGE,
            {
                "logger": Mock(),
                "requests": SimpleNamespace(get=self.request),
                "DOWNLOAD_TIMEOUT": 1,
                "MAX_AUDIO_FILE_SIZE": 1024,
            },
        )
        self.ns.update(
            _is_fagi_storage_url=lambda url: False,
            _existing_rehosted_audio=self.existing,
            _detected_audio_extension=lambda audio: "wav",
            download_audio_from_url_async=async_download,
        )

    def convert(self, mode="sync", project="synthetic-project"):
        args = ("synthetic-call", "https://recording.invalid/audio")
        kwargs = {"project_id": project, "provider": "synthetic"}
        if mode == "sync":
            return self.ns["convert_audio_url_to_s3_sync"](*args, **kwargs)
        return immediate(
            self.ns["_convert_audio_url_to_s3_async_with_size"](*args, **kwargs)
        )

    def test_paused_or_unreadable_store_prevents_all_rehost_io(self):
        for mode in ("sync", "async"):
            for error in (
                Refused("synthetic pause"),
                RuntimeError("synthetic store failure"),
            ):
                for authenticated in (False, True):
                    with self.subTest(
                        mode=mode,
                        error=type(error).__name__,
                        authenticated=authenticated,
                    ):
                        self.check.side_effect = error
                        self.service.is_authenticated_download.return_value = (
                            authenticated
                        )
                        result = self.convert(mode)
                        self.assertEqual(result, ("https://recording.invalid/audio", 0))
                        self.check.assert_called_with("synthetic-project")
                        self.existing.assert_not_called()
                        self.request.assert_not_called()
                        self.download.assert_not_called()
                        self.upload.assert_not_called()

    def test_pause_between_download_and_upload_prevents_storage(self):
        for mode in ("sync", "async"):
            with self.subTest(mode=mode):
                self.check.reset_mock(side_effect=True)
                self.check.side_effect = [None, Refused("new pause")]
                self.upload.reset_mock()
                result = self.convert(mode)
                self.assertEqual(result, ("https://recording.invalid/audio", 0))
                self.upload.assert_not_called()
                self.check.assert_called_with(
                    "synthetic-project", amount=len(b"synthetic-audio")
                )

    def test_unpaused_checks_before_download_and_upload(self):
        for mode in ("sync", "async"):
            with self.subTest(mode=mode):
                events = []
                self.check.side_effect = lambda *args, events=events, **kwargs: (
                    events.append("check")
                )
                self.existing.side_effect = lambda *args, events=events: events.append(
                    "existing"
                )
                self.download.side_effect = lambda *args, events=events, **kwargs: (
                    events.append("download") or b"synthetic-audio"
                )
                self.service.is_authenticated_download.return_value = True
                self.upload.side_effect = lambda *args, events=events, **kwargs: (
                    events.append("upload") or "synthetic-stored"
                )
                self.assertEqual(
                    self.convert(mode), ("synthetic-stored", len(b"synthetic-audio"))
                )
                self.assertEqual(
                    events, ["check", "existing", "download", "check", "upload"]
                )

    def test_check_is_fresh_for_each_artifact_in_actual_retell_loop(self):
        self.check.side_effect = [None, None, Refused("pause before stereo")]
        ns = functions(
            ROOT / "tracer/utils/retell.py",
            {
                "logger": Mock(),
                "VapiRecordingService": self.service,
                "_RETELL_RECORDING_KEY_BY_ARTIFACT_TYPE": {
                    "mono_combined": "mono",
                    "stereo": "stereo",
                },
                "convert_audio_url_to_s3_sync": self.ns["convert_audio_url_to_s3_sync"],
            },
            {"_rehost_recording_urls_sync"},
        )
        attrs = {
            "mono": "https://recording.invalid/mono",
            "stereo": "https://recording.invalid/stereo",
        }
        uploads = ns["_rehost_recording_urls_sync"](
            {"call_id": "synthetic-call"}, attrs, project_id="synthetic-project"
        )
        self.assertEqual(uploads, {"mono_combined": len(b"synthetic-audio")})
        self.assertEqual(attrs["stereo"], "https://recording.invalid/stereo")
        self.assertEqual(self.upload.call_count, 1)

    def test_actual_poll_with_refused_collector_has_no_paused_storage_usage(self):
        self.check.side_effect = Refused("paused")
        provider = SimpleNamespace(
            provider="retell",
            project=SimpleNamespace(
                id="synthetic-project", organization_id="synthetic-org"
            ),
        )
        emitted = Mock()

        def normalize(log, project_id):
            _, size = self.ns["convert_audio_url_to_s3_sync"](
                "synthetic-call",
                "https://recording.invalid/audio",
                project_id=project_id,
            )
            return {
                "id": "synthetic-call",
                "rehost_uploads": {"mono": size} if size else {},
            }

        ns = functions(
            ROOT / "tracer/utils/observability_provider.py",
            {
                "logger": Mock(),
                "ProviderChoices": SimpleNamespace(
                    VAPI="vapi",
                    RETELL="retell",
                    ELEVEN_LABS="eleven",
                    BLAND="bland",
                    TWILIO="twilio",
                ),
                "normalize_retell_data": normalize,
                "normalize_eleven_labs_data": Mock(),
                "normalize_twilio_data": Mock(),
                "_create_observation_span": Mock(),
                "_export_provider_call_to_collector": Mock(return_value=0),
                "emit_span_ingestion_usage": emitted,
                "_rehost_billing_event_id": Mock(return_value="synthetic-event"),
                "StoreOutcome": lambda *args: args,
            },
            {"process_and_store_logs"},
        )
        self.assertEqual(ns["process_and_store_logs"]([{}], provider), (0, 0, 1))
        self.upload.assert_not_called()
        emitted.assert_not_called()

    def test_projectless_async_recording_keeps_canonical_call_scope(self):
        result = self.convert("async", project=None)
        self.assertEqual(result, ("synthetic-stored", len(b"synthetic-audio")))
        self.check.assert_any_call(None, call_execution_id="synthetic-call")
        self.check.assert_called_with(
            None, call_execution_id="synthetic-call", amount=len(b"synthetic-audio")
        )


def deny_sockets(event, args):
    if event.startswith("socket."):
        raise PermissionError("socket-free control boundary")


if __name__ == "__main__":
    sys.addaudithook(deny_sockets)
    try:
        sys.audit("socket.connect", None, ("synthetic", 443))
    except PermissionError:
        pass
    else:
        raise AssertionError("socket guard did not refuse")
    unittest.main()
