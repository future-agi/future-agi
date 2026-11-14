import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { palette } from "src/theme/palette";

vi.mock("src/components/iconify", () => {
  const Icon = ({ icon }) => <span data-testid="icon" data-icon={icon} />;
  Icon.propTypes = { icon: PropTypes.string };
  return { default: Icon };
});

import OSSUpgradeGate from "../oss-upgrade-gate";

function renderGate(feature, reasonCode) {
  return render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <MemoryRouter>
        <OSSUpgradeGate feature={feature} reasonCode={reasonCode} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

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

describe("the Instant eval and Insights upgrade previews", () => {
  // The previews are invented example runs. A backdrop blur leaves the sharp
  // preview painted under it, and Chromium thins that blur toward the layer's
  // edges, so the last line of the Insights preview stayed readable.
  it.each([
    ["instantEval", undefined],
    ["instantEval", "LICENSE_MISSING"],
    ["instantEval", "LICENSE_FEATURE_MISSING"],
    ["instantEval", "LICENSE_EXPIRED"],
    ["instantEval", "LICENSE_INVALID"],
    ["insights", undefined],
    ["insights", "LICENSE_MISSING"],
    ["insights", "LICENSE_FEATURE_MISSING"],
    ["insights", "LICENSE_EXPIRED"],
    ["insights", "LICENSE_INVALID"],
  ])("blur the whole %s preview itself (%s)", (feature, reasonCode) => {
    const { container } = renderGate(feature, reasonCode);
    const previews = container.querySelectorAll("img");
    expect(previews).toHaveLength(1);
    const [preview] = previews;

    // Everything the preview paints sits inside one element that blurs it.
    const blurred = [];
    for (let el = preview; el && el !== container; el = el.parentElement) {
      if (/(^|[\s;])filter:\s*blur\(16px\)/.test(declarationsOf(el))) {
        blurred.push(el);
      }
    }
    expect(blurred).toHaveLength(1);
    expect(blurred[0]).toContainElement(preview);

    // No layer blurs what is behind it or fades toward an edge.
    for (const el of container.querySelectorAll("*")) {
      expect(declarationsOf(el)).not.toMatch(/backdrop-filter|mask/);
    }
  });
});
