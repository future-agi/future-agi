"""Model serving is optional: the standalone install runs ``serving`` only with the
``ml`` compose profile.

Every feature that needs embeddings must then fail open — skip the metric,
summarise without clustering, or fail with the reason — instead of returning a
400, marking work FAILED, or waiting on a DNS lookup and three retries per call.
They all gate on one cached probe, ``serving_available()``.

The root conftest reports serving as reachable for every test; the tests here
that need it absent request ``model_serving_down``. DB-free throughout.
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest
import requests
import yaml

from agentic_eval.core.embeddings import serving_client
from agentic_eval.core.embeddings.serving_client import (
    SERVING_PROBE_TTL_SECONDS,
    SERVING_UNAVAILABLE_MESSAGE,
    ModelServingClient,
    ServingUnavailableError,
    require_serving,
    serving_available,
)

# Bound at import, before the conftest fixture swaps in its stand-in.
REAL_SERVING_PROBE = serving_client._probe

SYSTEM_EVALS = Path(__file__).resolve().parents[1] / "system_evals" / "function"


def _counting_probe(monkeypatch, verdict):
    calls = []

    def probe(base_url):
        calls.append(base_url)
        return verdict

    monkeypatch.setattr(serving_client, "_probe", probe)
    return calls


def _closed_port_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


@pytest.fixture
def embed_server():
    """A stand-in serving that embeds every text as the same unit vector.
    Yields ``(base_url, requested_paths)``."""
    paths = []

    class Embed(BaseHTTPRequestHandler):
        def do_POST(self):
            paths.append(self.path)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps({"embeddings": [[1.0, 0.0]] * len(body["text"])})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Embed)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", paths
    server.shutdown()
    server.server_close()


# ─────────────────────────────────────────────────────────────────────
# The helper
# ─────────────────────────────────────────────────────────────────────


class TestServingAvailable:
    def test_an_empty_url_is_unavailable_without_a_probe(self, monkeypatch):
        """``MODEL_SERVING_URL=`` switches serving off outright."""
        monkeypatch.setenv("MODEL_SERVING_URL", "")
        calls = _counting_probe(monkeypatch, True)

        assert serving_available() is False
        assert calls == []

    def test_a_missing_serving_is_probed_once_per_ttl(self, monkeypatch):
        """The point of the cache: an absent host costs one lookup, not one per
        embedding call."""
        monkeypatch.setenv("MODEL_SERVING_URL", "http://serving:8080")
        calls = _counting_probe(monkeypatch, False)

        assert [serving_available() for _ in range(5)] == [False] * 5
        assert calls == ["http://serving:8080"]

    def test_the_verdict_is_reprobed_after_the_ttl(self, monkeypatch):
        """Enabling the ml profile is picked up without a restart."""
        monkeypatch.setenv("MODEL_SERVING_URL", "http://serving:8080")
        calls = _counting_probe(monkeypatch, False)
        assert serving_available() is False

        available, probed_at = serving_client._probe_cache["http://serving:8080"]
        serving_client._probe_cache["http://serving:8080"] = (
            available,
            probed_at - SERVING_PROBE_TTL_SECONDS - 1,
        )
        monkeypatch.setattr(serving_client, "_probe", lambda base_url: True)

        assert serving_available() is True
        assert len(calls) == 1

    def test_the_cache_is_keyed_on_the_url(self, monkeypatch):
        calls = _counting_probe(monkeypatch, False)
        monkeypatch.setenv("MODEL_SERVING_URL", "http://serving:8080")
        serving_available()
        monkeypatch.setenv("MODEL_SERVING_URL", "http://other-serving:8080/")
        serving_available()

        assert calls == ["http://serving:8080", "http://other-serving:8080"]

    def test_use_cache_false_always_probes(self, monkeypatch):
        calls = _counting_probe(monkeypatch, True)
        serving_available()
        serving_available(use_cache=False)

        assert len(calls) == 2

    def test_the_real_probe_is_quick_and_false_when_nothing_listens(self, monkeypatch):
        monkeypatch.setattr(serving_client, "_probe", REAL_SERVING_PROBE)
        monkeypatch.setenv("MODEL_SERVING_URL", _closed_port_url())

        assert serving_available() is False

    @pytest.mark.parametrize(
        "routes, expected",
        [
            ({"/health": 200}, True),
            # An older serving image and the E2E mock answer only the model
            # list, which is what the client's health_check() has always used.
            ({"/model/v1/models": 200}, True),
            ({}, False),
            # A server error is an answer: serving is there and unhealthy.
            ({"/health": 503, "/model/v1/models": 200}, False),
        ],
    )
    def test_the_real_probe_falls_back_to_the_model_list(
        self, monkeypatch, routes, expected
    ):
        requested = []

        class Serving(BaseHTTPRequestHandler):
            def do_GET(self):
                requested.append(self.path)
                self.send_response(routes.get(self.path, 404))
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Serving)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            monkeypatch.setattr(serving_client, "_probe", REAL_SERVING_PROBE)
            monkeypatch.setenv(
                "MODEL_SERVING_URL", f"http://127.0.0.1:{server.server_address[1]}"
            )

            assert serving_available(use_cache=False) is expected
        finally:
            server.shutdown()
            server.server_close()
        assert requested[0] == "/health"
        assert ("/model/v1/models" in requested) is (routes.get("/health") is None)

    def test_a_failed_client_call_marks_serving_unavailable(self, monkeypatch):
        """Serving that disappears after a good probe is not retried by every
        caller until the TTL runs out."""
        base = "http://serving-that-died:8080"
        calls = _counting_probe(monkeypatch, True)
        client = ModelServingClient(base_url=base)

        with patch.object(
            client.session,
            "post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(ConnectionError):
                client.embed_text("hello")

        assert serving_available(base) is False
        assert calls == [base]

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.embed_text("hello"),
            lambda c: c.embed_text_batch(["hello"]),
            lambda c: c.get_syn_data_embedding(["hello"]),
            lambda c: c.get_embeddings(["hello"], "openai", "text-embedding-3-small"),
        ],
        ids=["embed_text", "embed_text_batch", "syn_data", "infer"],
    )
    def test_the_client_fails_fast_while_serving_is_down(
        self, call, model_serving_down
    ):
        """No request, so no DNS lookup, connect timeout or adapter retries."""
        client = ModelServingClient(base_url="http://serving:8080")

        with patch.object(client.session, "post") as post:
            with pytest.raises(ServingUnavailableError):
                call(client)

        post.assert_not_called()


class TestServingUnavailableError:
    def test_require_serving_raises_the_guidance(self, model_serving_down):
        with pytest.raises(ServingUnavailableError) as excinfo:
            require_serving()

        assert "--profile ml" in str(excinfo.value)

    def test_it_is_caught_by_existing_connection_error_handlers(self):
        assert isinstance(ServingUnavailableError(), ConnectionError)

    def test_eval_errors_show_the_message_not_a_generic_failure(self):
        """Dataset eval cells render ``get_specific_error_message``, which only
        passes a ValueError's text through."""
        from tfc.utils.error_codes import get_specific_error_message

        assert (
            get_specific_error_message(ServingUnavailableError())
            == SERVING_UNAVAILABLE_MESSAGE
        )


def test_importing_the_embedding_manager_does_not_probe(monkeypatch):
    """The ModelManager singleton is built at import; a probe there stalled
    every module that imports it while `serving` failed to resolve."""
    from agentic_eval.core.embeddings.embedding_manager import ModelManager

    def fail(base_url):
        raise AssertionError("probed during initialisation")

    monkeypatch.setattr(serving_client, "_probe", fail)
    manager = object.__new__(ModelManager)
    manager._initialize()

    assert manager._serving_client is None


def test_rag_retrieval_returns_nothing_without_model_serving(model_serving_down):
    """Ground-truth and feedback few-shots fail open to no examples, without
    trying to embed each input first."""
    from agentic_eval.core.embeddings import embedding_manager as em

    with patch.object(em, "ClickHouseVectorDB"):
        manager = em.EmbeddingManager()
    with patch.object(manager, "retrieve_rag_based_examples") as per_input:
        result = manager.retrieve_avg_rag_based_examples(
            eval_id="tpl",
            inputs=["what is the refund policy"],
            input_cols=["question"],
            table_name=em.GROUND_TRUTH_TABLE_NAME,
            organization_id="org",
        )

    assert result == []
    per_input.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Annotation summary: the similarity metric is skipped, the rest stays
# ─────────────────────────────────────────────────────────────────────


def _text_metrics(**rows):
    return pd.DataFrame(
        [
            {"row_id": row_id, "user_id": f"u{i}", "value": text}
            for row_id, texts in rows.items()
            for i, text in enumerate(texts)
        ]
    )


class TestAnnotationSimilarity:
    @pytest.fixture
    def view(self):
        from model_hub.views.develop_annotations import AnnotationSummaryView

        return AnnotationSummaryView()

    def test_skipped_with_a_note_without_model_serving(self, view, model_serving_down):
        with patch("model_hub.views.develop_annotations.EmbeddingManager") as em:
            value, note = view.text_agreement(
                _text_metrics(r1=["a good answer", "a fine answer"])
            )

        assert value is None
        assert note == SERVING_UNAVAILABLE_MESSAGE
        em.assert_not_called()

    def test_computed_when_serving_is_up(self, view):
        with patch("model_hub.views.develop_annotations.EmbeddingManager") as em:
            em.return_value.get_syn_embedding.return_value = lambda batch: [
                [1.0, 0.0] for _ in batch
            ]
            value, note = view.text_agreement(
                _text_metrics(r1=["a good answer", "a fine answer"])
            )

        assert value == 1.0
        assert note is None

    def test_serving_dropping_mid_request_also_degrades(self, view):
        def unreachable(batch):
            raise ConnectionError("Failed to connect to serving service")

        with patch("model_hub.views.develop_annotations.EmbeddingManager") as em:
            em.return_value.get_syn_embedding.return_value = unreachable
            value, note = view.text_agreement(
                _text_metrics(r1=["a good answer", "a fine answer"])
            )

        assert (value, note) == (None, SERVING_UNAVAILABLE_MESSAGE)

    def test_single_annotator_rows_need_no_serving(self, view, model_serving_down):
        """Nothing to compare, so nothing is missing: no note either."""
        assert view.text_agreement(_text_metrics(r1=["only one"])) == (None, None)


# ─────────────────────────────────────────────────────────────────────
# Critical issues (eval reason summary): summarised without clustering
# ─────────────────────────────────────────────────────────────────────


def _explanations(n):
    return [{"id": f"call-{i}", "text": f"reason number {i}"} for i in range(n)]


@pytest.fixture
def explanation_agent():
    from ee.agenthub.explanation_agent.exp_agent import ExplanationAgent

    embed = Mock(side_effect=lambda batch: [[1.0, 0.0] for _ in batch])
    with patch("ee.agenthub.explanation_agent.exp_agent.EmbeddingManager") as em:
        em.return_value.get_syn_embedding.return_value = embed
        agent = ExplanationAgent(llm=Mock())
    agent.summarize_cluster = Mock(
        side_effect=lambda cluster_id, reps_texts, *args, **kwargs: {
            "theme": "Answers ignore the question",
            "kind": "failure",
            "status": "accepted",
        }
    )
    return agent


class TestExplanationSummaryWithoutEmbeddings:
    def test_all_explanations_are_summarised_as_one_group(
        self, explanation_agent, model_serving_down
    ):
        clusters = explanation_agent.evaluate(
            explanation=_explanations(25), eval_name="relevance"
        )

        explanation_agent.embedding_model.assert_not_called()
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster["theme"] == "Answers ignore the question"
        assert cluster["size"] == 25
        assert cluster["member_ids"] == [f"call-{i}" for i in range(25)]
        # An evenly spread sample, not just the first rows.
        assert len(cluster["representative_ids"]) == 10
        assert cluster["representative_ids"][0] == "call-0"
        assert cluster["representative_ids"][-1] == "call-24"

    def test_a_failing_embedding_call_falls_back_the_same_way(self, explanation_agent):
        """It used to np.stack whatever was embedded before the failure: an
        empty list raised, a partial one mislabelled every later explanation."""
        explanation_agent.embedding_model.side_effect = [
            [[1.0, 0.0]] * 10,
            ConnectionError("Failed to connect to serving service"),
        ]

        clusters = explanation_agent.evaluate(explanation=_explanations(25))

        assert len(clusters) == 1
        assert clusters[0]["size"] == 25

    def test_the_dataset_summary_completes_instead_of_failing(
        self, explanation_agent, model_serving_down
    ):
        from model_hub.models.choices import EvalExplanationSummaryStatus
        from model_hub.utils import eval_reasons

        dataset = Mock()
        reasons = {
            "relevance": {
                "eval_reasons": [f"reason number {i}" for i in range(20)],
                "user_eval_metric_id": "uem-1",
                "eval_template_id": "tpl-1",
                "eval_template_name": "relevance",
            }
        }
        with (
            patch.object(eval_reasons.Dataset.objects, "get", return_value=dataset),
            patch.object(eval_reasons.Row.objects, "filter") as rows,
            patch.object(eval_reasons, "get_eval_reasons", return_value=reasons),
            patch.object(
                eval_reasons, "ExplanationAgent", return_value=explanation_agent
            ),
        ):
            rows.return_value.count.return_value = 20
            eval_reasons.get_explanation_summary._original_func("ds-1")

        assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
        [cluster] = dataset.eval_reasons["relevance"]
        assert cluster["eval_template_id"] == "tpl-1"


@pytest.mark.parametrize(
    "n,k,expected",
    [
        (3, 10, [0, 1, 2]),
        (25, 3, [0, 12, 24]),
        (25, 1, [0]),
        (25, 0, []),
    ],
)
def test_evenly_spaced_indices(n, k, expected):
    from ee.agenthub.explanation_agent.exp_agent import ExplanationAgent

    assert ExplanationAgent.evenly_spaced_indices(n, k) == expected


# ─────────────────────────────────────────────────────────────────────
# Ground truth and the Vector DB column: a clear error
# ─────────────────────────────────────────────────────────────────────


def test_vector_db_row_records_the_serving_error(model_serving_down):
    """Every embedding type routes through serving; the row carries the reason
    instead of the embedding call's timeout."""
    from model_hub.views.dynamic_columns import AddVectorDBColumnView

    config = {"sub_type": "pinecone", "embedding_config": {"type": "openai"}}
    with (
        patch(
            "model_hub.views.dynamic_columns.Cell.objects.get",
            return_value=Mock(value="refund policy"),
        ),
        patch("model_hub.views.dynamic_columns.model_manager") as manager,
    ):
        value, value_infos = AddVectorDBColumnView()._process_row(
            Mock(), Mock(), config, "org"
        )

    assert value == SERVING_UNAVAILABLE_MESSAGE
    assert value_infos == {"reason": SERVING_UNAVAILABLE_MESSAGE}
    manager.get_embeddings.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Embedding system evals: fast, clear error; configured URL
# ─────────────────────────────────────────────────────────────────────


class TestEmbeddingEvalPreprocessing:
    @pytest.mark.parametrize(
        "eval_name", ["embedding_similarity", "semantic_list_contains"]
    )
    def test_fails_fast_without_model_serving(self, eval_name, model_serving_down):
        from evaluations.engine.preprocessing import preprocess_inputs

        with pytest.raises(ServingUnavailableError):
            preprocess_inputs(eval_name, {"output": "a", "expected": "b"})

    @pytest.mark.parametrize(
        "eval_name", ["embedding_similarity", "semantic_list_contains"]
    )
    def test_passes_the_configured_url_to_the_sandbox(self, eval_name, monkeypatch):
        from evaluations.engine.preprocessing import preprocess_inputs

        monkeypatch.setenv("MODEL_SERVING_URL", "http://ml-host:9000/")
        inputs = preprocess_inputs(eval_name, {"output": "a", "expected": "b"})

        assert inputs["_model_serving_url"] == "http://ml-host:9000"

    def test_clip_score_fails_fast_without_model_serving(self, model_serving_down):
        from evaluations.engine.preprocessing import preprocess_inputs

        with (
            patch(
                "evaluations.engine.preprocessing._resolve_image_input_as_data_uri"
            ) as fetch,
            pytest.raises(ServingUnavailableError),
        ):
            preprocess_inputs(
                "clip_score", {"images": "https://example.com/a.png", "text": "a cat"}
            )
        fetch.assert_not_called()

    def test_clip_score_without_inputs_is_left_to_the_eval(self, model_serving_down):
        from evaluations.engine.preprocessing import preprocess_inputs

        assert preprocess_inputs("clip_score", {"images": "", "text": ""}) == {
            "images": "",
            "text": "",
        }

    def test_other_preprocessing_failures_are_still_swallowed(self):
        from evaluations.engine import preprocessing

        with patch.dict(
            preprocessing.PREPROCESSORS,
            {"broken": Mock(side_effect=RuntimeError("boom"))},
        ):
            assert preprocessing.preprocess_inputs("broken", {"x": 1}) == {"x": 1}


def _system_eval(name):
    code = yaml.safe_load((SYSTEM_EVALS / f"{name}.yaml").read_text())["config"]["code"]
    namespace = {}
    exec(code, namespace)  # noqa: S102 - trusted repo fixture
    return namespace["evaluate"]


class TestEmbeddingEvalCodeUsesTheConfiguredUrl:
    """The seeded code used to hardcode ``http://serving:8080``."""

    def test_embedding_similarity(self, embed_server):
        base_url, paths = embed_server
        evaluate = _system_eval("embedding_similarity")

        result = evaluate(
            None, "the cat sat", "a cat was sitting", None, _model_serving_url=base_url
        )

        assert result["score"] == 1.0
        assert paths == ["/model/v1/embed"]

    def test_semantic_list_contains(self, embed_server):
        base_url, paths = embed_server
        evaluate = _system_eval("semantic_list_contains")

        result = evaluate(
            None,
            "Refunds take five days.",
            '["money back"]',
            None,
            _model_serving_url=base_url,
        )

        assert result["score"] == 1.0
        assert paths == ["/model/v1/embed"]
