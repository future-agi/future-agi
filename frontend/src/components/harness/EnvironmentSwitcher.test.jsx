import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import EnvironmentSwitcher from "./EnvironmentSwitcher";

const job = (id, name, stage) => ({
  job: { job_id: id, run_id: `harness-${id}`, metadata: { agent_name: name } },
  status: { stage, updated_at: "2026-08-25T10:00:00Z" },
  credentials: { detected_connectors: ["http", "livekit"] },
});

const jobs = [
  job("a", "ride-voice-e2e-3", "understanding_agent"),
  job("b", "ride-voice-smoke-1", "failed"),
];

describe("EnvironmentSwitcher", () => {
  it("keeps the create row as the last item in the menu", async () => {
    const user = userEvent.setup();
    render(
      <EnvironmentSwitcher
        jobs={jobs}
        currentJobId="a"
        onSelect={vi.fn()}
        onCreate={vi.fn()}
      />,
    );

    await user.click(screen.getByLabelText("Switch RL environment"));

    const items = within(screen.getByRole("menu")).getAllByRole("menuitem");
    expect(items).toHaveLength(jobs.length + 1);
    expect(items.at(-1)).toHaveTextContent("Create RL environment");
  });

  it("reports the environment that was picked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <EnvironmentSwitcher
        jobs={jobs}
        currentJobId="a"
        onSelect={onSelect}
        onCreate={vi.fn()}
      />,
    );

    await user.click(screen.getByLabelText("Switch RL environment"));
    await user.click(screen.getByText("ride-voice-smoke-1"));

    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("runs the create action from the pinned row", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    render(
      <EnvironmentSwitcher
        jobs={jobs}
        currentJobId="a"
        onSelect={vi.fn()}
        onCreate={onCreate}
      />,
    );

    await user.click(screen.getByLabelText("Switch RL environment"));
    await user.click(screen.getByText("Create RL environment"));

    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  // On the create route the pinned row would lead back to the page you are already on.
  it("suppresses the create row when asked to", async () => {
    const user = userEvent.setup();
    render(
      <EnvironmentSwitcher
        jobs={jobs}
        currentName="New environment"
        onSelect={vi.fn()}
        onCreate={vi.fn()}
        showCreate={false}
      />,
    );

    await user.click(screen.getByLabelText("Switch RL environment"));

    const items = within(screen.getByRole("menu")).getAllByRole("menuitem");
    expect(items).toHaveLength(jobs.length);
    expect(screen.queryByText("Create RL environment")).toBeNull();
  });

  // A cold load of a detail URL can leave the list unresolved; a dropdown onto nothing but
  // the create row would be worse than no dropdown at all.
  it("degrades to a plain label when there are no environments to switch between", () => {
    render(
      <EnvironmentSwitcher
        jobs={[]}
        currentJobId="a"
        currentName="ride-voice-e2e-3"
        onSelect={vi.fn()}
        onCreate={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("Switch RL environment")).toBeNull();
    expect(screen.getByText("ride-voice-e2e-3")).toBeInTheDocument();
  });
});
