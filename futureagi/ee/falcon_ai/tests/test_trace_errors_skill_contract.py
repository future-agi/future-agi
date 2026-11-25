"""Keep the trace-error skill honest about the background analysis hold."""

from ee.falcon_ai.builtin_skills_loader import load_builtin_skills


def test_trace_errors_skill_does_not_promise_background_feed_results():
    skill = next(
        s for s in load_builtin_skills() if s["slug"] == "analyze-trace-errors"
    )
    instructions = skill["instructions"]
    # analyze_project_traces refuses every request (#4001), so nothing reaches the Feed.
    assert "will appear on the Feed tab" not in instructions
    assert "background batch" not in instructions
    assert "Background multi-trace analysis is currently unavailable." in instructions
    assert "Never promise background or Feed results." in instructions
    # The prompt builder renders the first two trajectories as worked examples.
    shown = [
        step["tool"]
        for trajectory in skill["example_trajectories"][:2]
        for step in trajectory["steps"]
    ]
    assert "analyze_project_traces" not in shown
