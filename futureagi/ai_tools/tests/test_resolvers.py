"""
Pure unit tests for ai_tools/resolvers.py.

All Django ORM calls are mocked — no database or Django setup needed.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from ai_tools.resolvers import (
    is_uuid,
    resolve_dataset,
    resolve_eval_template,
    resolve_experiment,
    resolve_project,
    resolve_prompt_template,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_UUID = "550e8400-e29b-41d4-a716-446655440000"
VALID_UUID_2 = "660e8400-e29b-41d4-a716-446655440001"


def _make_mock_obj(name="Test Object", obj_id=None):
    """Create a mock model instance with .id and .name."""
    obj = MagicMock()
    obj.id = obj_id or uuid.uuid4()
    obj.name = name
    return obj


def _make_mock_org():
    return MagicMock(name="MockOrg")


def _make_mock_workspace():
    return MagicMock(name="MockWorkspace")


# ===========================================================================
# is_uuid
# ===========================================================================


class TestIsUuid:
    def test_valid_uuid_v4(self):
        assert is_uuid(VALID_UUID) is True

    def test_valid_uuid_uppercase(self):
        assert is_uuid(VALID_UUID.upper()) is True

    def test_valid_uuid_no_dashes(self):
        assert is_uuid(VALID_UUID.replace("-", "")) is True

    def test_invalid_uuid(self):
        assert is_uuid("not-a-uuid") is False

    def test_empty_string(self):
        assert is_uuid("") is False

    def test_random_text(self):
        assert is_uuid("my-dataset-name") is False

    def test_almost_uuid(self):
        # One character short
        assert is_uuid("550e8400-e29b-41d4-a716-44665544000") is False

    def test_none_coerced(self):
        # is_uuid does str(value), so None becomes "None"
        assert is_uuid(None) is False


# ===========================================================================
# resolve_dataset
# ===========================================================================


class TestResolveDataset:
    @patch("ai_tools.resolvers.Dataset")
    def test_resolve_by_uuid_found(self, MockDataset):
        org = _make_mock_org()
        ds = _make_mock_obj("My Dataset", obj_id=VALID_UUID)
        MockDataset.objects.get.return_value = ds

        result, err = resolve_dataset(VALID_UUID, org)
        assert result is ds
        assert err is None
        MockDataset.objects.get.assert_called_once_with(
            id=VALID_UUID, organization=org, deleted=False
        )

    @patch("ai_tools.resolvers.Dataset")
    def test_resolve_by_uuid_not_found(self, MockDataset):
        org = _make_mock_org()
        MockDataset.DoesNotExist = type("DoesNotExist", (Exception,), {})
        MockDataset.objects.get.side_effect = MockDataset.DoesNotExist

        result, err = resolve_dataset(VALID_UUID, org)
        assert result is None
        assert VALID_UUID in err
        assert "not found" in err.lower()

    @patch("ai_tools.resolvers.Dataset")
    def test_resolve_by_exact_name(self, MockDataset):
        org = _make_mock_org()
        ds = _make_mock_obj("Revenue Data")

        qs = MagicMock()
        qs.count.return_value = 1
        qs.first.return_value = ds
        MockDataset.objects.filter.return_value = qs

        result, err = resolve_dataset("Revenue Data", org)
        assert result is ds
        assert err is None

    @patch("ai_tools.resolvers.Dataset")
    def test_resolve_by_exact_name_with_workspace(self, MockDataset):
        org = _make_mock_org()
        ws = _make_mock_workspace()
        ds = _make_mock_obj("Revenue Data")

        qs_initial = MagicMock()
        qs_filtered = MagicMock()
        qs_filtered.count.return_value = 1
        qs_filtered.first.return_value = ds
        qs_initial.filter.return_value = qs_filtered
        MockDataset.objects.filter.return_value = qs_initial

        result, err = resolve_dataset("Revenue Data", org, workspace=ws)
        assert result is ds
        assert err is None
        # Verify workspace filter was applied
        qs_initial.filter.assert_called_once_with(workspace=ws)

    @patch("ai_tools.resolvers.Dataset")
    def test_resolve_multiple_matches(self, MockDataset):
        org = _make_mock_org()
        ds1 = _make_mock_obj("Revenue", obj_id=uuid.uuid4())
        ds2 = _make_mock_obj("Revenue", obj_id=uuid.uuid4())

        qs = MagicMock()
        qs.count.return_value = 2
        qs.__getitem__ = MagicMock(return_value=[ds1, ds2])
        MockDataset.objects.filter.return_value = qs

        result, err = resolve_dataset("Revenue", org)
        assert result is None
        assert "Multiple datasets" in err
        assert "specify the exact id" in err.lower()

    @patch("ai_tools.resolvers.Dataset")
    def test_resolve_by_fuzzy_name(self, MockDataset):
        org = _make_mock_org()
        ds = _make_mock_obj("Revenue Q4 2025")

        # Exact match returns 0
        exact_qs = MagicMock()
        exact_qs.count.return_value = 0
        # Fuzzy returns results. The resolver does `Dataset.objects.filter(...)[:5]`,
        # so __getitem__ must return something with .exists() and __iter__ working.
        # Easiest: have it return the same mock so configured methods stay applied.
        fuzzy_qs = MagicMock()
        fuzzy_qs.exists.return_value = True
        fuzzy_qs.__iter__ = MagicMock(return_value=iter([ds]))
        fuzzy_qs.__getitem__ = MagicMock(return_value=fuzzy_qs)

        MockDataset.objects.filter.side_effect = [exact_qs, fuzzy_qs]

        result, err = resolve_dataset("revenue", org)
        assert result is None
        assert "Did you mean" in err
        assert "Revenue Q4 2025" in err

    @patch("ai_tools.resolvers.Dataset")
    def test_resolve_no_match_at_all(self, MockDataset):
        org = _make_mock_org()

        exact_qs = MagicMock()
        exact_qs.count.return_value = 0
        fuzzy_qs = MagicMock()
        fuzzy_qs.exists.return_value = False
        fuzzy_qs.__getitem__ = MagicMock(return_value=fuzzy_qs)

        MockDataset.objects.filter.side_effect = [exact_qs, fuzzy_qs]

        result, err = resolve_dataset("nonexistent", org)
        assert result is None
        assert "No dataset found" in err

    def test_resolve_empty_identifier(self):
        org = _make_mock_org()
        result, err = resolve_dataset("", org)
        assert result is None
        assert "required" in err.lower()

    def test_resolve_none_identifier(self):
        org = _make_mock_org()
        result, err = resolve_dataset(None, org)
        # None is falsy, should hit the early return
        assert result is None
        assert err is not None


# ===========================================================================
# resolve_project
# ===========================================================================


class TestResolveProject:
    @patch("ai_tools.resolvers.Project")
    def test_resolve_by_uuid_found(self, MockProject):
        org = _make_mock_org()
        proj = _make_mock_obj("My Project", obj_id=VALID_UUID)
        MockProject.objects.get.return_value = proj

        result, err = resolve_project(VALID_UUID, org)
        assert result is proj
        assert err is None

    @patch("ai_tools.resolvers.Project")
    def test_resolve_by_uuid_not_found(self, MockProject):
        org = _make_mock_org()
        MockProject.DoesNotExist = type("DoesNotExist", (Exception,), {})
        MockProject.objects.get.side_effect = MockProject.DoesNotExist

        result, err = resolve_project(VALID_UUID, org)
        assert result is None
        assert "not found" in err.lower()

    @patch("ai_tools.resolvers.Project")
    def test_resolve_by_name(self, MockProject):
        org = _make_mock_org()
        proj = _make_mock_obj("TraceProject")

        qs = MagicMock()
        qs.count.return_value = 1
        qs.first.return_value = proj
        MockProject.objects.filter.return_value = qs

        result, err = resolve_project("TraceProject", org)
        assert result is proj
        assert err is None

    @patch("ai_tools.resolvers.Project")
    def test_resolve_by_name_multiple_matches(self, MockProject):
        org = _make_mock_org()
        p1 = _make_mock_obj("Prod", obj_id=uuid.uuid4())
        p2 = _make_mock_obj("Prod", obj_id=uuid.uuid4())

        qs = MagicMock()
        qs.count.return_value = 2
        qs.__getitem__ = MagicMock(return_value=[p1, p2])
        MockProject.objects.filter.return_value = qs

        result, err = resolve_project("Prod", org)
        assert result is None
        assert "Multiple projects" in err

    @patch("ai_tools.resolvers.Project")
    def test_resolve_no_match(self, MockProject):
        org = _make_mock_org()

        qs = MagicMock()
        qs.count.return_value = 0
        MockProject.objects.filter.return_value = qs

        result, err = resolve_project("ghost", org)
        assert result is None
        assert "No project found" in err

    def test_resolve_empty_identifier(self):
        org = _make_mock_org()
        result, err = resolve_project("", org)
        assert result is None
        assert "required" in err.lower()


# ===========================================================================
# resolve_eval_template
# ===========================================================================


class TestResolveEvalTemplate:
    @patch("ai_tools.resolvers.EvalTemplate")
    def test_resolve_by_uuid_found(self, MockTemplate):
        org = _make_mock_org()
        tmpl = _make_mock_obj("Faithfulness", obj_id=VALID_UUID)
        MockTemplate.objects.get.return_value = tmpl

        result, err = resolve_eval_template(VALID_UUID, org)
        assert result is tmpl
        assert err is None

    @patch("ai_tools.resolvers.EvalTemplate")
    def test_resolve_by_uuid_not_found(self, MockTemplate):
        org = _make_mock_org()
        MockTemplate.DoesNotExist = type("DoesNotExist", (Exception,), {})
        MockTemplate.objects.get.side_effect = MockTemplate.DoesNotExist

        result, err = resolve_eval_template(VALID_UUID, org)
        assert result is None
        assert "not found" in err.lower()

    @patch("ai_tools.resolvers.EvalTemplate")
    def test_resolve_by_name(self, MockTemplate):
        org = _make_mock_org()
        tmpl = _make_mock_obj("Hallucination")

        qs = MagicMock()
        qs.count.return_value = 1
        qs.first.return_value = tmpl
        MockTemplate.objects.filter.return_value = qs

        result, err = resolve_eval_template("Hallucination", org)
        assert result is tmpl
        assert err is None

    @patch("ai_tools.resolvers.EvalTemplate")
    def test_resolve_by_name_multiple_matches(self, MockTemplate):
        org = _make_mock_org()
        t1 = _make_mock_obj("Custom Eval", obj_id=uuid.uuid4())
        t2 = _make_mock_obj("Custom Eval", obj_id=uuid.uuid4())

        qs = MagicMock()
        qs.count.return_value = 2
        qs.__getitem__ = MagicMock(return_value=[t1, t2])
        MockTemplate.objects.filter.return_value = qs

        result, err = resolve_eval_template("Custom Eval", org)
        assert result is None
        assert "Multiple templates" in err

    @patch("ai_tools.resolvers.EvalTemplate")
    def test_resolve_fuzzy_suggestions(self, MockTemplate):
        org = _make_mock_org()
        tmpl = _make_mock_obj("hallucination_detection")

        exact_qs = MagicMock()
        exact_qs.count.return_value = 0
        # Resolver does `EvalTemplate.objects.filter(...)[:5]` — make __getitem__
        # return the same mock so configured exists/__iter__ stay applied.
        fuzzy_qs = MagicMock()
        fuzzy_qs.exists.return_value = True
        fuzzy_qs.__iter__ = MagicMock(return_value=iter([tmpl]))
        fuzzy_qs.__getitem__ = MagicMock(return_value=fuzzy_qs)

        MockTemplate.objects.filter.side_effect = [exact_qs, fuzzy_qs]

        result, err = resolve_eval_template("hallucination", org)
        assert result is tmpl
        assert err is None

    @patch("ai_tools.resolvers.EvalTemplate")
    def test_resolve_no_match(self, MockTemplate):
        org = _make_mock_org()

        exact_qs = MagicMock()
        exact_qs.count.return_value = 0
        fuzzy_qs = MagicMock()
        fuzzy_qs.exists.return_value = False
        fuzzy_qs.__getitem__ = MagicMock(return_value=fuzzy_qs)

        MockTemplate.objects.filter.side_effect = [exact_qs, fuzzy_qs]

        result, err = resolve_eval_template("nonexistent", org)
        assert result is None
        assert "No eval template found" in err

    def test_resolve_empty_identifier(self):
        org = _make_mock_org()
        result, err = resolve_eval_template("", org)
        assert result is None
        assert "required" in err.lower()


# ===========================================================================
# resolve_experiment
# ===========================================================================


class TestResolveExperiment:
    @patch("ai_tools.resolvers.ExperimentsTable")
    def test_resolve_by_uuid_found(self, MockExperiment):
        org = _make_mock_org()
        exp = _make_mock_obj("Baseline v1", obj_id=VALID_UUID)
        MockExperiment.objects.get.return_value = exp

        result, err = resolve_experiment(VALID_UUID, org)
        assert result is exp
        assert err is None
        MockExperiment.objects.get.assert_called_once_with(
            id=VALID_UUID,
            dataset__organization=org,
            deleted=False,
        )

    @patch("ai_tools.resolvers.ExperimentsTable")
    def test_resolve_by_uuid_not_found(self, MockExperiment):
        org = _make_mock_org()
        MockExperiment.DoesNotExist = type("DoesNotExist", (Exception,), {})
        MockExperiment.objects.get.side_effect = MockExperiment.DoesNotExist

        result, err = resolve_experiment(VALID_UUID, org)
        assert result is None
        assert "not found" in err.lower()

    @patch("ai_tools.resolvers.ExperimentsTable")
    def test_resolve_by_name(self, MockExperiment):
        org = _make_mock_org()
        exp = _make_mock_obj("Baseline v1")

        qs = MagicMock()
        qs.count.return_value = 1
        qs.first.return_value = exp
        MockExperiment.objects.filter.return_value = qs

        result, err = resolve_experiment("Baseline v1", org)
        assert result is exp
        assert err is None
        MockExperiment.objects.filter.assert_called_once_with(
            name__iexact="Baseline v1",
            dataset__organization=org,
            deleted=False,
        )

    @patch("ai_tools.resolvers.ExperimentsTable")
    def test_resolve_no_match(self, MockExperiment):
        org = _make_mock_org()

        qs = MagicMock()
        qs.count.return_value = 0
        MockExperiment.objects.filter.return_value = qs

        result, err = resolve_experiment("ghost", org)
        assert result is None
        assert "No experiment found" in err

    def test_resolve_empty_identifier(self):
        org = _make_mock_org()
        result, err = resolve_experiment("", org)
        assert result is None
        assert "required" in err.lower()


# ===========================================================================
# resolve_prompt_template
# ===========================================================================


class TestResolvePromptTemplate:
    @patch("ai_tools.resolvers.PromptTemplate")
    def test_resolve_by_uuid_found(self, MockPT):
        org = _make_mock_org()
        pt = _make_mock_obj("Summarizer", obj_id=VALID_UUID)
        MockPT.objects.get.return_value = pt

        result, err = resolve_prompt_template(VALID_UUID, org)
        assert result is pt
        assert err is None

    @patch("ai_tools.resolvers.PromptTemplate")
    def test_resolve_by_uuid_not_found(self, MockPT):
        org = _make_mock_org()
        MockPT.DoesNotExist = type("DoesNotExist", (Exception,), {})
        MockPT.objects.get.side_effect = MockPT.DoesNotExist

        result, err = resolve_prompt_template(VALID_UUID, org)
        assert result is None
        assert "not found" in err.lower()

    @patch("ai_tools.resolvers.PromptTemplate")
    def test_resolve_by_name(self, MockPT):
        org = _make_mock_org()
        pt = _make_mock_obj("Summarizer")

        qs = MagicMock()
        qs.count.return_value = 1
        qs.first.return_value = pt
        MockPT.objects.filter.return_value = qs

        result, err = resolve_prompt_template("Summarizer", org)
        assert result is pt
        assert err is None

    @patch("ai_tools.resolvers.PromptTemplate")
    def test_resolve_by_name_with_workspace(self, MockPT):
        org = _make_mock_org()
        ws = _make_mock_workspace()
        pt = _make_mock_obj("Summarizer")

        qs_initial = MagicMock()
        qs_filtered = MagicMock()
        qs_filtered.count.return_value = 1
        qs_filtered.first.return_value = pt
        qs_initial.filter.return_value = qs_filtered
        MockPT.objects.filter.return_value = qs_initial

        result, err = resolve_prompt_template("Summarizer", org, workspace=ws)
        assert result is pt
        assert err is None
        qs_initial.filter.assert_called_once_with(workspace=ws)

    @patch("ai_tools.resolvers.PromptTemplate")
    def test_resolve_by_name_multiple_matches(self, MockPT):
        org = _make_mock_org()
        p1 = _make_mock_obj("Template", obj_id=uuid.uuid4())
        p2 = _make_mock_obj("Template", obj_id=uuid.uuid4())

        qs = MagicMock()
        qs.count.return_value = 2
        qs.__getitem__ = MagicMock(return_value=[p1, p2])
        MockPT.objects.filter.return_value = qs

        result, err = resolve_prompt_template("Template", org)
        assert result is None
        assert "Multiple templates" in err

    @patch("ai_tools.resolvers.PromptTemplate")
    def test_resolve_no_match(self, MockPT):
        org = _make_mock_org()

        qs = MagicMock()
        qs.count.return_value = 0
        MockPT.objects.filter.return_value = qs

        result, err = resolve_prompt_template("nonexistent", org)
        assert result is None
        assert "No prompt template found" in err

    def test_resolve_empty_identifier(self):
        org = _make_mock_org()
        result, err = resolve_prompt_template("", org)
        assert result is None
        assert "required" in err.lower()
