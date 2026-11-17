import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";

import { toneColor } from "../components/ToneChip";

const sourceOf = (file) =>
  readFileSync(resolve(process.cwd(), "src/sections/omega/hub", file), "utf8");

const tones = {
  attention: "warning",
  success: "success",
  error: "danger",
  info: "info",
  primary: "info",
  waiting: "info",
};
const luminance = (hex) => {
  const channels = hex.match(/[a-f\d]{2}/gi).map((part) => {
    const n = parseInt(part, 16) / 255;
    return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
};
const contrast = (a, b) => {
  const values = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (values[1] + 0.05) / (values[0] + 0.05);
};

describe.each(["light", "dark"])(
  "HUB-UX-01 status foregrounds in %s",
  (mode) => {
    const p = palette(mode);
    it.each(Object.entries(tones))(
      "%s uses the canonical %s foreground",
      (tone, status) => {
        const color = toneColor({ palette: p }, tone);
        expect(color).toBe(p.status[status].fg);
        expect(contrast(color, p.background.paper)).toBeGreaterThanOrEqual(4.5);
      },
    );
    it("keeps neutral readable and uses AA metadata text", () => {
      expect(toneColor({ palette: p }, "neutral")).toBe(p.text.secondary);
      expect(
        contrast(p.text.tertiary, p.background.paper),
      ).toBeGreaterThanOrEqual(4.5);
    });
  },
);

// Closed census from HUB-UX-02: only the audited content, not decorative
// icons, separators, inactive dots, hover borders or unrelated P2/P3 findings.
const content = [
  ["components/EntryHeader.jsx", 1],
  ["components/FiltersBar.jsx", 2],
  ["components/UploadCard.jsx", 1],
  ["HubSettingsPage.jsx", 1],
  ["HubSubmissionsPage.jsx", 1],
  ["HubSubmissionPage.jsx", 2],
  ["components/StatusTimeline.jsx", 2],
  ["components/VersionsTable.jsx", 1],
  ["components/Tombstone.jsx", 1],
  ["components/RemixBlock.jsx", 1],
  ["components/EntryCard.jsx", 2],
  ["components/PicksStrip.jsx", 1],
  ["components/InstallsStat.jsx", 1],
  ["components/EvidencePanel.jsx", 1],
  ["components/CheckReport.jsx", 1],
  // R2-UX-06 removes the misleading private-note footer; TextField now owns
  // the truthful helper. Keep the zero count pinned, not a file exemption.
  ["components/ModerateMenu.jsx", 0],
  ["components/ReportDialog.jsx", 1],
  ["components/PublisherSettings.jsx", 1],
  ["components/CopyCommand.jsx", 1],
  ["HubReviewQueuePage.jsx", 1],
  ["HubAuditPage.jsx", 1],
  ["HubReportsPage.jsx", 1],
  ["components/ChangeNotesList.jsx", 1],
  ["components/KeysPanel.jsx", 2],
  ["components/FileTree.jsx", 1],
  ["components/RulesView.jsx", 1],
];

it("HUB-UX-01 tracker dots, including numbered future steps, use the status map", () => {
  const source = sourceOf("components/StatusTimeline.jsx");
  expect(source).not.toContain("theme.palette.text.disabled");
  expect(source).toContain("toneColor(theme, dot.tone)");
});

describe("HUB-UX-02 readable metadata", () => {
  it.each(content)(
    "%s uses tertiary for all %i audited content instances",
    (file, count) => {
      const source = sourceOf(file);
      expect(source.match(/text\.tertiary/g) || []).toHaveLength(count);
      expect(source).not.toMatch(/<Typography\b[^>]*text\.disabled/);
    },
  );
});
