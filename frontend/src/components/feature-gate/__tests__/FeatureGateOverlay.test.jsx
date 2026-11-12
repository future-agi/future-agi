import React from "react";
import { describe, expect, it, vi } from "vitest";

import { render, screen } from "src/utils/test-utils";

import FeatureGateOverlay from "../FeatureGateOverlay";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

// What a caller of the old wall passed: a product screenshot per scheme.
const OLD_PREVIEW = {
  image: "/assets/falcon_ai_light-abc123.png",
  imageDark: "/assets/falcon_ai_dark-abc123.png",
  imageAlt: "Feature preview",
};
const TITLE = "Falcon AI is not available on this deployment";

describe("FeatureGateOverlay (the feature wall)", () => {
  it.each([false, true])(
    "never shows a preview image, even when one is passed (plain=%s)",
    (plain) => {
      const { container } = render(
        <FeatureGateOverlay {...OLD_PREVIEW} plain={plain} title={TITLE} />,
      );
      expect(container.querySelector("img")).toBeNull();
      expect(screen.queryByAltText("Feature preview")).toBeNull();
      expect(container.innerHTML).not.toMatch(/falcon_ai_(light|dark)/);
      expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
    },
  );

  it("shows no eyebrow unless the caller names one", () => {
    const { unmount } = render(<FeatureGateOverlay plain title={TITLE} />);
    expect(screen.queryByText("Cloud feature")).toBeNull();
    unmount();
    render(
      <FeatureGateOverlay plain eyebrow="Enterprise Edition" title={TITLE} />,
    );
    expect(screen.getByText("Enterprise Edition")).toBeInTheDocument();
  });
});
