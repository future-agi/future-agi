"""Agreement selection follows value changes, not unrelated notes or sources."""

import pytest

from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from model_hub.tests.test_eval_agreement import AgreementTenant


@pytest.fixture
def worlds(db):
    a, b = AgreementTenant("selection-a"), AgreementTenant("selection-b")
    yield a, b
    a.client.stop_workspace_injection()
    b.client.stop_workspace_injection()


def later_vote(tenant):
    return Score.no_workspace_objects.create(
        organization=tenant.org,
        workspace=tenant.workspace,
        dataset_row=tenant.rows[0],
        source_type="dataset_row",
        label=tenant.label,
        annotator=tenant.user,
        score_source="human",
        value={"selected": ["Fail"]},
    )


@pytest.mark.parametrize("fields", [None, ["notes", "updated_at"]])
def test_notes_on_older_vote_do_not_change_latest_value(worlds, fields):
    a, _ = worlds
    later_vote(a)
    before = a.read()["report"]
    assert before["accuracy"] == pytest.approx(1 / 3)
    old = a.scores[0]
    old.notes = "Clarify the original review without changing the vote."
    old.save(update_fields=fields)
    assert a.read()["report"] == before


def test_changed_value_becomes_latest_but_same_value_submission_does_not(worlds):
    a, _ = worlds
    newest = later_vote(a)
    old = a.scores[0]
    old.value = {"selected": ["Fail"]}
    old.save(update_fields=["value"])
    old.value = {"selected": ["Pass"]}
    old.save(update_fields=["value"])
    assert a.read()["report"]["accuracy"] == pytest.approx(2 / 3)
    newest.notes = "An unchanged value must not supersede the revised vote."
    newest.save()
    assert a.read()["report"]["accuracy"] == pytest.approx(2 / 3)


def test_value_timestamp_is_persisted_only_when_the_value_changes(worlds):
    a, _ = worlds
    old = a.scores[0]
    original = old.value_updated_at
    old.notes = "A note"
    old.save()
    old.refresh_from_db()
    assert old.value_updated_at == original
    old.value = {"selected": ["Fail"]}
    old.save(update_fields=["value"])
    old.refresh_from_db()
    assert old.value_updated_at > original
    changed = old.value_updated_at
    old.notes = "Another note"
    old.save(update_fields=["notes", "updated_at"])
    old.refresh_from_db()
    assert old.value_updated_at == changed


@pytest.mark.parametrize("large_name", ["zz-large-review", "aa-large-review"])
def test_large_unrelated_label_does_not_block_selected_source(worlds, large_name):
    a, b = worlds
    other = AnnotationsLabels.no_workspace_objects.create(
        name=large_name,
        type="categorical",
        organization=a.org,
        workspace=a.workspace,
        settings=a.label.settings,
    )
    Score.no_workspace_objects.bulk_create(
        [
            Score(
                organization=a.org,
                workspace=a.workspace,
                dataset_row=a.rows[0],
                source_type="dataset_row",
                label=other,
                annotator=a.user,
                score_source="human",
                value={"selected": ["Fail"]},
            )
            for _ in range(10001)
        ]
    )
    selected = f"{a.binding.id}:{a.label.id}"
    result = a.read(source=selected)
    assert result["report"]["key"] == selected
    assert result["report"]["n"] == 3
    assert result["report"]["accuracy"] == pytest.approx(2 / 3)
    assert {source["key"] for source in result["sources"]} == {
        selected,
        f"{a.binding.id}:{other.id}",
    }
    default = a.read()
    assert default["sources"] == result["sources"]
    if large_name.startswith("aa"):
        assert default["report"] is None
    else:
        assert default["report"]["key"] == selected
    assert (
        a.client.get(a.url, {"source": f"{a.binding.id}:{other.id}"}).status_code == 413
    )
    assert (
        a.client.get(a.url, {"source": f"{b.binding.id}:{b.label.id}"}).status_code
        == 404
    )


def test_source_must_match_explicit_dataset_and_label_filters(worlds):
    a, _ = worlds
    unrelated = a.make_dataset("other dataset")
    source = f"{a.binding.id}:{a.label.id}"
    assert (
        a.client.get(
            a.url, {"source": source, "dataset_id": str(unrelated.id)}
        ).status_code
        == 404
    )
