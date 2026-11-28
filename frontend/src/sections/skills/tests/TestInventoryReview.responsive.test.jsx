import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { dsPalette, dsTypography } from "src/theme/ds-tokens";
import TestInventoryReview, { flagText, formatBytes } from "./TestInventoryReview";

// The drawer reads the file; here only which file it was opened for matters.
vi.mock("./InventoryFileDrawer", () => ({ default: ({ file }) => <div role="dialog" aria-label={`File ${file.path}`} /> }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// UXF-03 / UXF-12 at 390 px: the answer-check and skill quick-check file table uses the
// shared stacked-table pattern (one tappable card per file), not crushed columns.
const preview = {
  fingerprint: "plan-fingerprint",
  candidate: { skillId: "ctx", digest: "candidate-digest", version: 2 },
  inventory: [{
    skillId: "ctx", digest: "candidate-digest", name: "support-agent",
    files: [
      { path: "AGENTS.md", sha256: "a".repeat(64), sizeBytes: 1536, flags: [] },
      { path: "skills/refund-policy/SKILL.md", sha256: "b".repeat(64), sizeBytes: 220, flags: ["case_overlap"] },
    ],
  }],
  outboundFindings: [],
};
const value = { fingerprint: "plan-fingerprint", reviewedAllFiles: false, noKnownAnswerKeys: false, classifications: [], falsePositiveSecretFindings: [] };

function show(width) {
  window.matchMedia = (query) => {
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    const min = /min-width:\s*([\d.]+)px/.exec(query);
    const matches = (!max || width <= Number(max[1])) && (!min || width >= Number(min[1]));
    return { matches, media: query, onchange: null, addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false };
  };
  return render(
    <ThemeProvider theme={createTheme({ palette: dsPalette("light"), typography: dsTypography })}>
      <MemoryRouter>
        <TestInventoryReview preview={preview} value={value} onChange={vi.fn()} subjectKind="agent_context" subjectId="ctx" subjectName="support-agent" />
      </MemoryRouter>
    </ThemeProvider>,
  );
}
afterEach(cleanup);

describe("model-visible inventory table", () => {
  it("says what each card value is without column headers", () => {
    expect(flagText([])).toBe("No flags");
    expect(flagText(["case_overlap", "evaluation_artifact"])).toBe("Flagged: case overlap, evaluation artifact");
    expect(formatBytes(1)).toBe("1 byte");
    expect(formatBytes(220)).toBe("220 bytes");
    expect(formatBytes(1536)).toBe("1.5 KB");
  });

  it("keeps the File, Source/version, Flags and Size columns on desktop with the row action in the trailing menu", () => {
    show(1440);
    const table = screen.getByRole("table", { name: "Model-visible inventory" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["File", "Source/version", "Flags", "Size", ""]);
    const row = within(table).getByRole("row", { name: "View skills/refund-policy/SKILL.md" });
    expect(within(row).getByText("Flagged: case overlap")).toBeInTheDocument();
    expect(within(row).getByText("220 bytes")).toBeInTheDocument();
    // No outlined or bare per-row button: the action is the row and its ⋯ menu.
    expect(within(row).queryByRole("button", { name: "View" })).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: "Actions for skills/refund-policy/SKILL.md" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "View file" }));
    expect(screen.getByRole("dialog", { name: "File skills/refund-policy/SKILL.md" })).toBeInTheDocument();
  });

  it("is a stack of tappable cards at 390 px: the row opens the file and ends in a chevron, not a button", () => {
    show(390);
    const table = screen.getByRole("table", { name: "Model-visible inventory" });
    const row = within(table).getByRole("row", { name: "View AGENTS.md" });
    expect(row).toHaveAttribute("data-row-link", "true");
    expect(row.querySelector('[data-cell="row-chevron"]')).not.toBeNull();
    expect(within(row).queryByRole("button", { name: /Actions for/ })).toBeNull();
    expect(within(row).getByText("No flags")).toBeInTheDocument();
    expect(within(row).getByText("1.5 KB")).toBeInTheDocument();
    fireEvent.click(within(row).getByText("AGENTS.md"));
    expect(screen.getByRole("dialog", { name: "File AGENTS.md" })).toBeInTheDocument();
  });
});
