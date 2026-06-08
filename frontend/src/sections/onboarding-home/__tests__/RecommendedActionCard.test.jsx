import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import { getActivationStateFixture } from "../fixtures/activation-state.fixtures";
import { normalizeActivationState } from "../activation-state-utils";
import RecommendedActionCard from "../components/RecommendedActionCard";

const actionFixture = () =>
  normalizeActivationState(getActivationStateFixture("observeNoSetup"))
    .recommendedAction;

const RECOVERY_DOCS_HREF = "https://docs.futureagi.com/docs";

describe("RecommendedActionCard", () => {
  it("renders an available action as an internal link", async () => {
    const onActionClick = vi.fn();
    render(
      <RecommendedActionCard
        action={actionFixture()}
        label="Next setup step"
        onActionClick={onActionClick}
      />,
    );

    expect(
      screen.getByRole("link", { name: /create observe project/i }),
    ).toHaveAttribute(
      "href",
      "/dashboard/observe?setup=true&source=onboarding",
    );

    await userEvent.click(
      screen.getByRole("link", { name: /create observe project/i }),
    );
    expect(onActionClick).toHaveBeenCalledWith(
      expect.objectContaining({ id: "create_observe_project" }),
    );
  });

  it("disables blocked actions and shows the blocker", () => {
    const action = {
      ...actionFixture(),
      href: null,
      blocked: true,
      blockedReason: "route_not_implemented",
      routeAvailable: false,
    };

    render(<RecommendedActionCard action={action} label="Next setup step" />);

    expect(screen.getByText("This setup step is not ready yet.")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /create observe project/i }),
    ).toBeDisabled();

    const recoveryLink = screen.getByRole("link", {
      name: /view setup guide/i,
    });
    expect(recoveryLink).toHaveAttribute("href", RECOVERY_DOCS_HREF);
    expect(recoveryLink).toHaveAttribute("target", "_blank");
    expect(recoveryLink).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("keeps docs as the primary recovery for a blocked path without an in-app sample", () => {
    // Observe has no path sample fixture, so the only honest recovery is docs;
    // it must stay the prominent (button) recovery, not get demoted.
    const action = {
      ...actionFixture(),
      href: null,
      blocked: true,
      blockedReason: "permission_limited",
      routeAvailable: false,
    };

    render(
      <RecommendedActionCard
        action={action}
        label="Next setup step"
        primaryPath="observe"
        onShowSample={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /see a sample instead/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /view setup guide/i }),
    ).toHaveAttribute("href", RECOVERY_DOCS_HREF);
  });

  it("makes the in-app sample the primary recovery and demotes docs for a blocked path with a sample", async () => {
    const onShowSample = vi.fn();
    const action = {
      ...actionFixture(),
      href: null,
      blocked: true,
      blockedReason: "route_not_implemented",
      routeAvailable: false,
    };

    render(
      <RecommendedActionCard
        action={action}
        label="Next setup step"
        primaryPath="prompt"
        onShowSample={onShowSample}
      />,
    );

    // Primary recovery is the in-app sample, rendered as a contained button.
    const sampleButton = screen.getByRole("button", {
      name: "See a sample instead",
    });
    expect(sampleButton).toBeVisible();

    // Docs are still present but demoted to a tertiary text link (kept,
    // secondary), still opening in a new tab.
    const recoveryLink = screen.getByRole("link", {
      name: /view setup guide/i,
    });
    expect(recoveryLink).toHaveAttribute("href", RECOVERY_DOCS_HREF);
    expect(recoveryLink).toHaveAttribute("target", "_blank");
    expect(recoveryLink).toHaveAttribute("rel", "noopener noreferrer");

    // Demotion is also positional: the sample (primary) renders before the
    // docs link (tertiary) in the recovery stack.
    expect(
      sampleButton.compareDocumentPosition(recoveryLink) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // Clicking the sample recovery only reveals the existing preview - it never
    // completes or advances setup, so no mutation is triggered here.
    await userEvent.click(sampleButton);
    expect(onShowSample).toHaveBeenCalledTimes(1);
  });

  it("does not offer the sample recovery when the action is not blocked", () => {
    const onShowSample = vi.fn();

    render(
      <RecommendedActionCard
        action={actionFixture()}
        label="Next setup step"
        primaryPath="prompt"
        onShowSample={onShowSample}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /see a sample instead/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /view setup guide/i }),
    ).not.toBeInTheDocument();
  });

  it("offers a forward recovery path when no action is available", () => {
    render(<RecommendedActionCard action={null} label="Next setup step" />);

    const recoveryLink = screen.getByRole("link", {
      name: /view setup guide/i,
    });
    expect(recoveryLink).toHaveAttribute("href", RECOVERY_DOCS_HREF);
    expect(recoveryLink).toHaveAttribute("target", "_blank");
    expect(recoveryLink).toHaveAttribute("rel", "noopener noreferrer");
  });
});
