// HO S8: every "Open in Agent Playground" opens the version the run ran. A
// Test a fix child records the version it saved in its config (v3.1) and
// copies its parent's target (v3); the leaderboard ⋯, the result drawer's
// output card and Run details all link v3.1, as Publish reads it.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const navigate = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useNavigate: () => navigate,
}));
vi.mock("src/sections/experiment-detail/results/RunSourceBlock", () => ({
  default: () => null,
  useRunWithConfig: (run) => ({ full: run, datasetId: run?.dataset_id }),
}));

import { agentPlaygroundUrl } from "src/sections/develop-detail/ExperimentTab/RunRowMenu";
import { agentPlaygroundHref } from "src/sections/experiment-detail/results/drawer/OutputCard";
import RunDetailsPopover from "src/sections/experiment-detail/results/RunDetailsPopover";
import { publishVersionIdOf } from "src/sections/experiment-detail/iterate/publishGate";

const PARENT = {
  run_id: "r-2b",
  label: "#2B",
  target_type: "agent",
  target: {
    graph_id: "g-support",
    graph_name: "Support Agent",
    version_id: "gv-3",
    version_name: "v3",
  },
  config: {
    agent: { graph_id: "g-support", version_id: "gv-3", version: "v3" },
  },
};
// Test a fix: the child copies the parent's target and names v3.1 in config.
const CHILD = {
  ...PARENT,
  run_id: "r-2h",
  label: "#2H",
  config: {
    agent: { graph_id: "g-support", version_id: "gv-3-1", version: "v3.1" },
  },
};

describe("agent playground links (HO S8)", () => {
  it("the leaderboard ⋯ link opens the child's v3.1 and the parent's v3", () => {
    expect(agentPlaygroundUrl(CHILD)).toBe(
      "/dashboard/agents/playground/g-support/build?version=gv-3-1",
    );
    expect(agentPlaygroundUrl(PARENT)).toBe(
      "/dashboard/agents/playground/g-support/build?version=gv-3",
    );
  });

  it("the output card link reads the run's config before its target", () => {
    // M11: the card passes the run's config to the one link rule
    // (targetLinks.agentLinkOf, M9), so the second argument is the config.
    expect(agentPlaygroundHref(CHILD.target, CHILD.config)).toBe(
      "/dashboard/agents/playground/g-support/build?version=gv-3-1",
    );
    expect(agentPlaygroundHref(PARENT.target, PARENT.config)).toBe(
      "/dashboard/agents/playground/g-support/build?version=gv-3",
    );
    // No run config: the target's version, as before.
    expect(agentPlaygroundHref(PARENT.target)).toBe(
      "/dashboard/agents/playground/g-support/build?version=gv-3",
    );
  });

  it("Run details opens the version the run ran", () => {
    const anchor = document.createElement("button");
    document.body.appendChild(anchor);
    for (const [run, id] of [
      [CHILD, "gv-3-1"],
      [PARENT, "gv-3"],
    ]) {
      navigate.mockClear();
      const { unmount } = render(
        <RunDetailsPopover anchorEl={anchor} onClose={() => {}} run={run} />,
      );
      fireEvent.click(
        screen.getByRole("button", { name: /Open in Agent Playground/ }),
      );
      // M11: Run details builds its link with the one rule (agentLinkOf, M9).
      expect(navigate).toHaveBeenCalledWith(
        `/dashboard/agents/playground/g-support/build?version=${id}`,
      );
      unmount();
    }
  });

  it("all three agree with the version Publish activates", () => {
    expect(publishVersionIdOf(CHILD)).toBe("gv-3-1");
    expect(agentPlaygroundUrl(CHILD)).toContain(publishVersionIdOf(CHILD));
    expect(agentPlaygroundHref(CHILD.target, CHILD.config)).toContain(
      publishVersionIdOf(CHILD),
    );
  });
});
