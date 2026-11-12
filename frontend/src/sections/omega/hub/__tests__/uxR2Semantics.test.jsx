import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import FiltersBar from "../components/FiltersBar";
import FileTree from "../components/FileTree";
import ModerateMenu from "../components/ModerateMenu";
import CountDefinition from "../components/CountDefinition";
import { StatusChip } from "../components/StatusTimeline";
import { StepStatus } from "../components/EvidencePanel";
import CheckReport from "../components/CheckReport";
import { statusMeta } from "../lib/status";
import { toneColor } from "../components/ToneChip";
import official from "../__fixtures__/entry-official.json";
import rules from "../__fixtures__/rules.json";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
const sourceOf = (file) =>
  readFileSync(resolve(process.cwd(), "src/sections/omega/hub", file), "utf8");
const rgb = (hex) => hex.match(/[a-f\d]{2}/gi).map((n) => parseInt(n, 16));
const cssRgb = (hex) => `rgb(${rgb(hex).join(", ")})`;
const luminance = (channels) =>
  channels
    .map((n) => {
      const c = n / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    })
    .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
const contrast = (a, b) => {
  const [lo, hi] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (hi + 0.05) / (lo + 0.05);
};

it.each(["light", "dark"])(
  "waiting and failed checks use DS info/danger roles in %s",
  (mode) => {
    const p = palette(mode);
    expect(toneColor({ palette: p }, statusMeta("in_review").tone)).toBe(
      p.status.info.fg,
    );
    for (const status of ["checks_failed", "check_error"])
      expect(toneColor({ palette: p }, statusMeta(status).tone)).toBe(
        p.status.danger.fg,
      );
  },
);

it("uses the same failure role in the submission chip, check step and finding", () => {
  renderAt(
    <>
      <StatusChip status="checks_failed" />
      <div data-testid="step">
        <StepStatus status="fail" />
      </div>
      <CheckReport
        report={{
          steps: [
            {
              id: "manifest",
              status: "fail",
              findings: [
                { id: "f", severity: "block", message: "Failed check" },
              ],
            },
          ],
        }}
      />
    </>,
  );
  const p = palette("light");
  const chip = getComputedStyle(screen.getByTestId("hub-status-chip")).color;
  expect(chip).toBe(cssRgb(p.status.danger.fg));
  expect(
    getComputedStyle(screen.getByTestId("step").firstElementChild).color,
  ).toBe(chip);
  expect(
    getComputedStyle(screen.getByTestId("hub-finding").firstElementChild).color,
  ).toBe(chip);
  expect(sourceOf("HubReviewQueuePage.jsx")).toContain(
    "<StatusChip status={row.status}",
  );
  for (const file of [
    "components/EvidencePanel.jsx",
    "components/CheckReport.jsx",
  ])
    expect(sourceOf(file)).toMatch(
      /import\s+\{[^}]*checkTone[^}]*\}\s+from\s+["']\.\.\/lib\/status["']/,
    );
});

it("uses secondary text for the selected filter count with AA contrast over the alpha fill", () => {
  renderAt(
    <FiltersBar
      params={{ kind: "agent" }}
      facets={{ kinds: { agent: 7 } }}
      onChange={() => {}}
    />,
  );
  const chip = screen.getByTestId("hub-filter-kind-agent");
  const p = palette("light");
  const count = within(chip).getByText("7");
  expect(getComputedStyle(count).color).toBe(cssRgb(p.text.secondary));
  // tokens.json color.bg.selected over color.bg.surface, not raw alpha.
  const [r, g, b, alpha] = getComputedStyle(chip)
    .backgroundColor.match(/[\d.]+/g)
    .map(Number);
  const surface = rgb(p.background.paper);
  const fill = [r, g, b].map((c, i) => c * alpha + surface[i] * (1 - alpha));
  expect(contrast(rgb(p.text.secondary), fill)).toBeGreaterThanOrEqual(4.5);
});

it("keeps file sizes readable on the selected file fill", () => {
  renderAt(
    <FileTree
      files={[{ path: "entry.js", bytes: 1024 }]}
      selected="entry.js"
    />,
  );
  const row = screen.getByTestId("hub-file-row");
  expect(getComputedStyle(within(row).getByText("1.0 KB")).color).toBe(
    cssRgb(palette("light").text.secondary),
  );
});

it.each(["light", "dark"])(
  "uses worst-severity FileTree marks and readable note tokens in %s",
  (mode) => {
    const p = palette(mode);
    renderAt(
      <ThemeProvider theme={createTheme({ palette: p })}>
        <FileTree
          files={[
            { path: "block.js", bytes: 1 },
            { path: "flag.js", bytes: 2 },
          ]}
          selected="block.js"
          findingsByFile={{
            "block.js": [{ severity: "flag" }, { severity: "block" }],
            "flag.js": [{ severity: "flag" }],
          }}
          notesByFile={{ "block.js": [{ note: "Please check this." }] }}
        />
      </ThemeProvider>,
    );
    const [blocked, flagged] = screen.getAllByTestId("hub-file-finding-mark");
    expect(getComputedStyle(blocked).color).toBe(cssRgb(p.status.danger.fg));
    expect(getComputedStyle(flagged).color).toBe(cssRgb(p.status.warning.fg));
    const note = screen.getByTitle("1 reviewer note");
    expect(getComputedStyle(note).color).toBe(cssRgb(p.status.info.fg));
    const [r, g, b, alpha] = getComputedStyle(
      screen.getAllByTestId("hub-file-row")[0],
    )
      .backgroundColor.match(/[\d.]+/g)
      .map(Number);
    const surface = rgb(p.background.paper);
    const fill = [r, g, b].map((c, i) => c * alpha + surface[i] * (1 - alpha));
    expect(contrast(rgb(p.status.info.fg), surface)).toBeGreaterThanOrEqual(
      4.5,
    );
    expect(contrast(rgb(p.status.info.fg), fill)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(rgb(p.status.danger.fg), fill)).toBeGreaterThanOrEqual(4.5);
  },
);

it.each(["light", "dark"])(
  "keeps selected warning findings readable on their DS badge fill in %s",
  (mode) => {
    const p = palette(mode);
    renderAt(
      <ThemeProvider theme={createTheme({ palette: p })}>
        <FileTree
          files={[{ path: "flag.js", bytes: 2 }]}
          selected="flag.js"
          findingsByFile={{ "flag.js": [{ severity: "flag" }] }}
        />
      </ThemeProvider>,
    );
    const composite = (css, underneath) => {
      const [r, g, b, a = 1] = css.match(/[\d.]+/g).map(Number);
      return [r, g, b].map((c, i) => c * a + underneath[i] * (1 - a));
    };
    const row = screen.getByTestId("hub-file-row");
    const mark = screen.getByTestId("hub-file-finding-mark");
    const style = getComputedStyle(mark);
    expect(style.color).toBe(cssRgb(p.status.warning.fg));
    const selected = composite(
      getComputedStyle(row).backgroundColor,
      rgb(p.background.paper),
    );
    const expectedBg = p.status.warning.bg.startsWith("#")
      ? cssRgb(p.status.warning.bg)
      : p.status.warning.bg;
    expect(style.backgroundColor.match(/[\d.]+/g).map(Number)).toEqual(
      expectedBg.match(/[\d.]+/g).map(Number),
    );
    expect(
      contrast(
        rgb(p.status.warning.fg),
        composite(style.backgroundColor, selected),
      ),
    ).toBeGreaterThanOrEqual(4.5);
  },
);

it("uses the DS icon role for count definitions at 3:1 or better", async () => {
  respond([["get", "/omega-hub/v1/rules", rules]]);
  renderAt(<CountDefinition kind="likes" />);
  const icon = await screen.findByTestId("hub-count-definition-likes");
  const p = palette("light");
  expect(getComputedStyle(icon).color).toBe(cssRgb(p.action.active));
  for (const mode of ["light", "dark"]) {
    const colors = palette(mode);
    expect(
      contrast(rgb(colors.action.active), rgb(colors.background.paper)),
    ).toBeGreaterThanOrEqual(3);
  }
});

it.each([
  [true, "Unrank", "Unrank", "error"],
  [true, "Yank version 1.0.0", "Yank", "error"],
  [false, "Rank again", "Re-rank", "primary"],
])(
  "styles the %s / %s confirmation consistently",
  (ranked, menuLabel, verb, color) => {
    respond([]);
    renderAt(<ModerateMenu entry={{ ...official, ranked }} version="1.0.0" />);
    fireEvent.click(screen.getByTestId("hub-moderate-button"));
    fireEvent.click(
      screen.getByRole("menuitem", { name: menuLabel, exact: true }),
    );
    const dialog = screen.getByRole("dialog");
    const button = within(dialog).getByRole("button", {
      name: verb,
      exact: true,
    });
    expect(button).toHaveClass(
      color === "error"
        ? "MuiButton-containedError"
        : "MuiButton-containedPrimary",
    );
  },
);
