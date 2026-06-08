import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import { normalizeActivationState } from "../activation-state-utils";
import PathCardGrid from "../components/PathCardGrid";

const pathsFixture = () =>
  normalizeActivationState(getActivationStateFixture("observeNoSetup"))
    .availablePaths;

describe("PathCardGrid", () => {
  it("renders path options and tracks available path clicks", async () => {
    const onPathClick = vi.fn();
    const promptPath = {
      id: "prompt",
      label: "Test prompts or agent prompts",
      description: "Create, test, and compare prompt versions.",
      status: "available",
      href: "/dashboard/home?path=prompt",
      isAvailable: true,
      blockedReason: null,
      requiresPermission: "prompt:write",
      firstActionId: "create_prompt",
    };

    render(
      <PathCardGrid
        paths={[...pathsFixture(), promptPath]}
        onPathClick={onPathClick}
      />,
    );

    expect(screen.getByText("Connect your agent")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /current setup/i }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /preview only/i }),
    ).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /choose this/i }));

    expect(onPathClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "prompt" }),
    );
  });

  it("disables unavailable paths", () => {
    const promptPath = {
      id: "prompt",
      label: "Test prompts or agent prompts",
      description: "Create, test, and compare prompt versions.",
      status: "hidden",
      href: "/dashboard/home?path=prompt",
      isAvailable: false,
      blockedReason: "route_not_implemented",
      requiresPermission: "prompt:write",
      firstActionId: "create_prompt",
    };

    render(<PathCardGrid paths={[promptPath]} />);

    expect(screen.getByText("This setup path is not ready yet.")).toBeVisible();
    expect(screen.getByRole("button", { name: /unavailable/i })).toBeDisabled();
  });
});
