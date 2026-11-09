import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import OSSUpgradeGate from "../oss-upgrade-gate";
import { FEATURES } from "../constants";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

const push = vi.fn();
vi.mock("src/routes/hooks", () => ({ useRouter: () => ({ push }) }));

describe("OSSUpgradeGate", () => {
  beforeEach(() => push.mockReset());

  it.each(["errorFeed", "optimization", "falconAI"])(
    "%s renders a neutral card with no preview image or chat history",
    (feature) => {
      const { container } = render(
        <OSSUpgradeGate feature={feature} reasonCode="EE_CODE_UNAVAILABLE" />,
      );
      expect(container.querySelector("img")).toBeNull();
      const text = container.textContent;
      expect(text).not.toMatch(
        /Build a dataset|jinja template|June 2026|April 2026/,
      );
      expect(text).not.toMatch(/coming soon|roadmap|star the repo/i);
      expect(text).not.toMatch(/Connect your tools/);
      expect(text).not.toMatch(/What to do next/);
      expect(
        screen.getByRole("heading", {
          name: `${FEATURES[feature].name} is not available on this deployment`,
        }),
      ).toBeInTheDocument();
    },
  );

  it("states what an open-source deployment needs and offers contact, not a license page", () => {
    render(
      <OSSUpgradeGate feature="falconAI" reasonCode="EE_CODE_UNAVAILABLE" />,
    );
    expect(screen.getByTestId("oss-gate-needs")).toHaveTextContent(
      /Enterprise Edition image and a license key/,
    );
    const primary = screen.getByRole("link", { name: /Talk to us/ });
    expect(primary).toHaveAttribute(
      "href",
      "https://futureagi.com/talk-to-human",
    );
  });

  it("sends a license denial to the License page", () => {
    render(<OSSUpgradeGate feature="errorFeed" reasonCode="LICENSE_EXPIRED" />);
    expect(screen.getByTestId("oss-gate-needs")).toHaveTextContent(
      /has expired/,
    );
    screen.getByRole("button", { name: /Manage license/ }).click();
    expect(push).toHaveBeenCalledWith("/dashboard/settings/ee-licenses");
  });

  it("names the plan on a cloud plan denial", () => {
    render(
      <OSSUpgradeGate feature="errorFeed" reasonCode="PLAN_FEATURE_MISSING" />,
    );
    expect(screen.getByTestId("oss-gate-needs")).toHaveTextContent(
      /plan that includes this feature/,
    );
  });

  it("falls back to a plain license need when no reason is given", () => {
    render(<OSSUpgradeGate feature="optimization" />);
    expect(screen.getByTestId("oss-gate-needs")).toHaveTextContent(
      "Needs an Enterprise license.",
    );
  });

  it("renders nothing for an unknown feature", () => {
    const { container } = render(<OSSUpgradeGate feature="nope" />);
    expect(container.textContent).toBe("");
  });
});
