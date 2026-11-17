import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { nudgeFromApi } from "src/api/simulate-environments/versions";
import NudgeCard from "../NudgeCard";

const UNAVAILABLE =
  "Private GitHub repositories are not available on Future AGI cloud. Upload the code to keep using this environment.";

const failedTurn = (error) => ({
  id: "m1",
  role: "system",
  kind: "nudge",
  nudgeId: "n1",
  text: "Agent v3 changed: prompt. Rebuild the environment?",
  nudge: nudgeFromApi({
    id: "n1",
    target: "environment",
    state: "failed",
    facts: { prompt_changed: true, agent_default_moved: true },
    trigger_agent_version: { id: "a3", label: "v3" },
    base_environment_version: { id: "e4", label: "v4" },
    replaced_by: null,
    can_stop: false,
    queued_behind_input: false,
    summary: null,
    error,
  }),
  nudgesEnabled: true,
  onRebuild: vi.fn(),
  onSkipNudge: vi.fn(),
  onStopNudge: vi.fn(),
});

describe("NudgeCard for a rebuild whose sandbox couldn't read the source", () => {
  it("says the private repository can't be read here and what keeps the environment going", () => {
    render(
      <NudgeCard turn={failedTurn({ code: "github_private_unavailable" })} />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(UNAVAILABLE);
    expect(screen.queryByText("The update failed.")).toBeNull();
    expect(screen.queryByText("The update didn't start in time.")).toBeNull();
  });

  it("still says a rebuild that never started didn't start in time", () => {
    render(<NudgeCard turn={failedTurn({ code: "rebuild_not_started" })} />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "The update didn't start in time.",
    );
    expect(screen.queryByText(UNAVAILABLE)).toBeNull();
  });
});
