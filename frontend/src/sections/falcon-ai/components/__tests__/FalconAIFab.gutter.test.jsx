import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import FalconAIFab from "../FalconAIFab";
import useFalconStore from "../../store/useFalconStore";
import {
  FAB_GUTTER_PX,
  FAB_GUTTER_VAR,
} from "src/sections/common/ux/useFabGutter";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";

const capability = { allowed: false, isLoading: false, isError: false };
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => capability,
}));

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));

// These are jsdom computed-CSS checks on the real MUI Fab and live gutter
// hook, not browser bounding boxes. Neither Fab nor its sx is mocked, and
// no getBoundingClientRect is fabricated. The Save-footer clearance below
// is CSS arithmetic, not rendered footer measurement or actual hit-testing.
// Real Save clickability at 1600x1100 still needs walk-lead's film or the
// EVAL-E2E-020 browser run on the rig.
function renderLauncherGeometry() {
  render(
    <MemoryRouter initialEntries={["/dashboard/develop"]}>
      <FalconAIFab />
    </MemoryRouter>,
  );
  const launcher = screen.getByRole("button", { name: "Open Falcon AI" });
  expect(launcher).toBeVisible();
  expect(document.body.style.getPropertyValue(FAB_GUTTER_VAR)).toBe(
    `${FAB_GUTTER_PX}px`,
  );

  const style = window.getComputedStyle(launcher);
  expect(style.position).toBe("fixed");
  expect(style.bottom).toMatch(/px$/);
  expect(style.height).toMatch(/px$/);
  const bottom = Number.parseFloat(style.bottom);
  const height = Number.parseFloat(style.height);
  expect(bottom).toBeGreaterThanOrEqual(0);
  expect(height).toBeGreaterThan(0);
  return { bottom, height };
}

describe("FalconAIFab gutter geometry (computed CSS)", () => {
  beforeEach(() => {
    useFalconStore.setState({ isSidebarOpen: false });
    useShortcutStore.getState().setPaletteOpen(false);
  });
  afterEach(cleanup);

  it("keeps the real floating launcher's computed footprint inside its reserved gutter", () => {
    const { bottom, height } = renderLauncherGeometry();
    expect(bottom + height).toBeLessThanOrEqual(FAB_GUTTER_PX);
  });

  it("keeps the computed launcher below the Save-footer content boundary at 1600x1100", () => {
    const { bottom, height } = renderLauncherGeometry();
    // At the standard 1600x1100 viewport with no prototype-label strip,
    // dashboard Main's bottom padding ends content (including the task
    // Save footer) one exported gutter above the viewport's bottom edge.
    const viewportHeight = 1100;
    const contentBottom = viewportHeight - FAB_GUTTER_PX;
    const launcherTop = viewportHeight - bottom - height;
    expect(launcherTop).toBeGreaterThanOrEqual(contentBottom);
  });
});
