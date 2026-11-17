import React from "react";
import { describe, expect, it, vi } from "vitest";

import { render, screen } from "src/utils/test-utils";

import FeatureGateOverlay from "../FeatureGateOverlay";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

// A product screenshot per scheme, as the gate passes for a feature that has
// a preview.
const PREVIEW = {
  image: "/assets/falcon_ai_light-abc123.png",
  imageDark: "/assets/falcon_ai_dark-abc123.png",
  imageAlt: "Feature preview",
};
const TITLE = "Falcon AI is not available on this deployment";

// The declarations emotion wrote for an element's own classes.
function declarationsOf(element) {
  const classes = Array.from(element.classList, (name) => `.${name}`);
  return Array.from(document.styleSheets)
    .flatMap((sheet) => Array.from(sheet.cssRules))
    .filter((rule) =>
      classes.some((name) => rule.selectorText?.split(/[\s:,]/)[0] === name),
    )
    .map((rule) => rule.style.cssText)
    .join(" ");
}

describe("FeatureGateOverlay (the feature wall)", () => {
  it("shows a given preview behind the card, never sharp all over", () => {
    const { container } = render(
      <FeatureGateOverlay {...PREVIEW} title={TITLE} />,
    );
    const preview = screen.getByAltText("Feature preview");
    expect(preview).toHaveAttribute("src", PREVIEW.image);
    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
    // Blurred below a sharp top unless the caller asks for the whole image.
    const belowTop = Array.from(container.querySelectorAll("*")).filter((el) =>
      /(^|[\s;])top:\s*38%/.test(declarationsOf(el)),
    );
    expect(belowTop).toHaveLength(1);
    expect(declarationsOf(belowTop[0])).toMatch(
      /(^|[\s;])backdrop-filter:\s*blur\(9px\)/,
    );
  });

  it("blurs the whole preview as one element when asked to", () => {
    const { container } = render(
      <FeatureGateOverlay {...PREVIEW} blurFrom={0} blur={16} title={TITLE} />,
    );
    const preview = screen.getByAltText("Feature preview");
    const blurred = [];
    for (let el = preview; el && el !== container; el = el.parentElement) {
      if (/(^|[\s;])filter:\s*blur\(16px\)/.test(declarationsOf(el))) {
        blurred.push(el);
      }
    }
    expect(blurred).toHaveLength(1);
  });

  it("shows no preview on a plain card, even when one is passed", () => {
    const { container } = render(
      <FeatureGateOverlay {...PREVIEW} plain title={TITLE} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("heading", { name: TITLE })).toBeInTheDocument();
  });

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
