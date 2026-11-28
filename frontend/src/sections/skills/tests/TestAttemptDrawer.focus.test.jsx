import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import TestAttemptDrawer from "./TestAttemptDrawer";

// UX iteration 2 (TST1): closing the attempt drawer returns focus to its opener without scrolling,
// so the dashboard frame and the document never move sideways.
vi.mock("src/api/skills", () => ({ getSkillErrorCode: () => undefined }));
vi.mock("src/api/skills/testing", () => ({
  useSkillTestAttempt: () => ({ data: undefined, isLoading: false, isError: false, refetch: () => {} }),
  useSkillTestFile: () => ({ data: undefined, isLoading: false, isError: false, refetch: () => {} }),
}));
vi.mock("../review/HumanCheckReviewController", () => ({ default: () => null }));
afterEach(cleanup);

function Opener() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Inspect attempt</button>
      <TestAttemptDrawer open={open} onClose={() => setOpen(false)} skillId="skill" runId="run" />
    </>
  );
}

it("restores focus to the opener with preventScroll after the drawer closes", async () => {
  const calls = [];
  const descriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "focus");
  const original = HTMLElement.prototype.focus;
  Object.defineProperty(HTMLElement.prototype, "focus", {
    configurable: true,
    writable: true,
    value(options) {
      calls.push([this, options]);
      return original.call(this, options);
    },
  });
  try {
    render(<MemoryRouter><Opener /></MemoryRouter>);
    const opener = screen.getByRole("button", { name: "Inspect attempt" });
    opener.focus();
    fireEvent.click(opener);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    calls.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "Close attempt evidence" }));
    await waitFor(() => expect(opener).toHaveFocus());
    const restores = calls.filter(([element]) => element === opener);
    expect(restores.length).toBeGreaterThan(0);
    for (const [, options] of restores) expect(options).toEqual({ preventScroll: true });
  } finally {
    Object.defineProperty(HTMLElement.prototype, "focus", descriptor);
  }
});
