from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SKILL = (
    REPOSITORY_ROOT / ".claude" / "skills" / "futureagi-slots" / "SKILL.md"
)
CODEX_ADAPTER = REPOSITORY_ROOT / ".agents" / "skills" / "futureagi-slots" / "SKILL.md"
FEATURE_GUIDE = CANONICAL_SKILL.parent / "references" / "feature-guide.md"


def test_claude_skill_contains_shared_runtime_contract() -> None:
    content = CANONICAL_SKILL.read_text()
    normalized = " ".join(content.split())

    assert "name: futureagi-slots" in content
    assert "Every slot always gets a private frontend" in normalized
    assert "explicitly approved local Docker" in normalized
    assert "SLOTS_RUNTIME_APPROVED=1 make slot-up" in normalized
    assert "Never invoke the slot Compose files directly" in normalized
    assert "make slot-purge SLOT=<slot> CONFIRM=<slot>" in content
    assert "references/feature-guide.md" in content
    assert "developer onboarding and feature overviews" in content


def test_codex_adapter_references_canonical_skill() -> None:
    content = CODEX_ADAPTER.read_text()

    assert "name: futureagi-slots" in content
    assert "../../../.claude/skills/futureagi-slots/SKILL.md" in content
    assert "Do not duplicate or override" in content
    assert "developer onboarding and feature overviews" in content


def test_feature_guide_covers_every_public_make_target() -> None:
    guide = FEATURE_GUIDE.read_text()
    makefile = (REPOSITORY_ROOT / "Makefile").read_text()
    phony_line = next(
        line for line in makefile.splitlines() if line.startswith(".PHONY:")
    )
    public_targets = set(phony_line.removeprefix(".PHONY:").split())

    assert public_targets
    assert all(target in guide for target in public_targets)
    assert "The root `Makefile` is the only supported entry and exit point" in guide
    assert "Every slot always owns a frontend" in guide
    assert "CONFIRM` must exactly equal `SLOT" in guide
    assert "root `.env` in each worktree" in guide
    assert "rerun `make slot-up`" in guide
