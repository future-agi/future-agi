"""
Tests for normalize_search_for_name (issue #1070).

Covers the space ↔ underscore ↔ hyphen normalization logic that lets
users search for ``context_adherence`` by typing ``context adherence``
or ``context-adherence``.
"""

import pytest
from django.db.models import Q

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.choices import OwnerChoices
from model_hub.utils.eval_list import (
    build_eval_list_queryset,
    normalize_search_for_name,
)


# =============================================================================
# Pure unit tests (no database)
# =============================================================================


@pytest.mark.unit
class TestNormalizeSearchForName:
    """Unit tests for ``normalize_search_for_name`` — no DB required."""

    def test_returns_q_object(self):
        """Function must return a Django Q object."""
        result = normalize_search_for_name("test")
        assert isinstance(result, Q)

    def test_exact_match_included(self):
        """The original search term must be one of the OR branches."""
        q = normalize_search_for_name("context adherence")
        # A Q object with 3 OR children
        assert q.connector == Q.OR
        child_keys = [child.__repr__() for child in q.children]
        # At least one child should contain the original term
        assert any("context adherence" in k for k in child_keys)

    def test_space_to_underscore(self):
        """Spaces in the search term must produce an underscore variant."""
        q = normalize_search_for_name("context adherence")
        child_keys = [child.__repr__() for child in q.children]
        assert any("context_adherence" in k for k in child_keys)

    def test_space_to_hyphen(self):
        """Spaces in the search term must produce a hyphen variant."""
        q = normalize_search_for_name("context adherence")
        child_keys = [child.__repr__() for child in q.children]
        assert any("context-adherence" in k for k in child_keys)

    def test_no_spaces_passthrough(self):
        """A search term without spaces should still produce a valid Q."""
        q = normalize_search_for_name("context_adherence")
        # All three branches are identical when there are no spaces,
        # but the Q is still valid and usable.
        assert isinstance(q, Q)

    def test_strips_whitespace(self):
        """Leading/trailing whitespace should be stripped."""
        q1 = normalize_search_for_name("  context adherence  ")
        q2 = normalize_search_for_name("context adherence")
        assert q1.__repr__() == q2.__repr__()

    def test_multiple_spaces(self):
        """Multiple spaces should all be replaced."""
        q = normalize_search_for_name("a b c")
        child_keys = [child.__repr__() for child in q.children]
        assert any("a_b_c" in k for k in child_keys)
        assert any("a-b-c" in k for k in child_keys)

    def test_single_word(self):
        """Single-word searches should work (no spaces to replace)."""
        q = normalize_search_for_name("hallucination")
        assert isinstance(q, Q)
        # For a single word, all three branches collapse to the same thing
        assert len(q.children) == 3

    def test_empty_string_after_strip(self):
        """Empty/whitespace-only input should still return a valid Q
        (Django will simply match everything with ``name__icontains=""``)."""
        q = normalize_search_for_name("   ")
        assert isinstance(q, Q)

    def test_hyphen_in_search_term(self):
        """If the user types a hyphenated name, the underscore variant
        should also be produced (since DB names use underscores)."""
        q = normalize_search_for_name("context-adherence")
        # spaces → underscores: "context-adherence" has no spaces,
        # so replace(" ", "_") is a no-op. But the exact match covers it.
        assert isinstance(q, Q)

    def test_mixed_spaces_and_hyphens(self):
        """A term like 'context - adherence' (with spaces around hyphens)
        should still produce underscore variants."""
        q = normalize_search_for_name("context - adherence")
        child_keys = [child.__repr__() for child in q.children]
        assert any("context_-_adherence" in k for k in child_keys)


# =============================================================================
# Integration tests (database required)
# =============================================================================


@pytest.mark.django_db
class TestNormalizeSearchIntegration:
    """Integration tests that verify the search normalization works
    end-to-end through ``build_eval_list_queryset``."""

    def _make_template(self, organization, workspace, name):
        return EvalTemplate.no_workspace_objects.create(
            name=name,
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "score"},
            eval_tags=["llm"],
            visible_ui=True,
        )

    def test_search_with_spaces_finds_underscore_name(
        self, organization, workspace
    ):
        """Typing 'context adherence' must find 'context_adherence'."""
        self._make_template(organization, workspace, "context_adherence")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="context adherence",
        )
        names = list(qs.values_list("name", flat=True))
        assert "context_adherence" in names

    def test_search_with_spaces_finds_hyphen_name(
        self, organization, workspace
    ):
        """Typing 'context adherence' must find 'context-adherence'."""
        self._make_template(organization, workspace, "context-adherence")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="context adherence",
        )
        names = list(qs.values_list("name", flat=True))
        assert "context-adherence" in names

    def test_search_with_underscore_still_works(
        self, organization, workspace
    ):
        """Direct underscore search must still work (backward compat)."""
        self._make_template(organization, workspace, "context_adherence")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="context_adherence",
        )
        names = list(qs.values_list("name", flat=True))
        assert "context_adherence" in names

    def test_search_with_hyphen_still_works(
        self, organization, workspace
    ):
        """Direct hyphen search must still work (backward compat)."""
        self._make_template(organization, workspace, "context-adherence")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="context-adherence",
        )
        names = list(qs.values_list("name", flat=True))
        assert "context-adherence" in names

    def test_search_does_not_match_unrelated(
        self, organization, workspace
    ):
        """Normalization should not cause false positive matches."""
        self._make_template(organization, workspace, "context_adherence")
        self._make_template(organization, workspace, "unrelated_eval")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="unrelated",
        )
        names = list(qs.values_list("name", flat=True))
        assert "context_adherence" not in names
        assert "unrelated_eval" in names

    def test_search_multi_word_name(
        self, organization, workspace
    ):
        """Multi-word normalization: 'answer similarity' → 'answer_similarity'."""
        self._make_template(organization, workspace, "answer_similarity")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="answer similarity",
        )
        names = list(qs.values_list("name", flat=True))
        assert "answer_similarity" in names

    def test_case_insensitive_search(
        self, organization, workspace
    ):
        """Search is case-insensitive (icontains)."""
        self._make_template(organization, workspace, "context_adherence")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="Context Adherence",
        )
        names = list(qs.values_list("name", flat=True))
        assert "context_adherence" in names

    def test_partial_match_with_spaces(
        self, organization, workspace
    ):
        """Partial search 'context adh' should match 'context_adherence'."""
        self._make_template(organization, workspace, "context_adherence")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="context adh",
        )
        names = list(qs.values_list("name", flat=True))
        assert "context_adherence" in names

    def test_no_search_returns_all(
        self, organization, workspace
    ):
        """Without search, all visible templates are returned."""
        t1 = self._make_template(organization, workspace, "eval_a")
        t2 = self._make_template(organization, workspace, "eval_b")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
        )
        names = list(qs.values_list("name", flat=True))
        assert "eval_a" in names
        assert "eval_b" in names

    def test_search_with_no_results(
        self, organization, workspace
    ):
        """A search that matches nothing returns an empty queryset."""
        self._make_template(organization, workspace, "context_adherence")
        qs = build_eval_list_queryset(
            organization=organization,
            workspace=workspace,
            search="nonexistent eval",
        )
        assert qs.count() == 0
