// Public link choices and words (W4-6, A3).
import { describe, expect, it } from "vitest";
import {
  anyoneLine,
  defaultExpiry,
  earlierLine,
  expiryChoices,
  expiryLabel,
  includeLabels,
  isActive,
  pausedLine,
  publicUrl,
  versionLabel,
  viewsLine,
} from "../publicLinkModel";

const NOW = Date.parse("2026-10-22T12:00:00Z");
const link = {
  url_path: "/shared/abc",
  expires_at: "2026-10-29T12:00:00Z",
  include: { rows: true, experiments: true, feedback: false },
  version: "frozen",
  version_number: 6,
  views: 0,
  last_opened_at: null,
  revoked_at: null,
};

describe("publicLinkModel", () => {
  // Review fix (W4 A must-fix 5): the choices are the server's (the policy's
  // expiry_choices / default_expiry_days, w4-a-shareLinks.test.js pins how
  // the server computes them); the dialog only keeps them within the max.
  it("expiry choices are the server's, never past the policy maximum", () => {
    const policy = (max_days, expiry_choices, default_expiry_days) => ({
      max_days,
      expiry_choices,
      default_expiry_days,
    });
    expect(expiryChoices(policy(30, [1, 7, 30], 7))).toEqual([1, 7, 30]);
    expect(expiryChoices(policy(5, [1, 5], 5))).toEqual([1, 5]);
    expect(expiryChoices(policy(5, [5, 1, 1, 7, 2.5, "x"], 5))).toEqual([1, 5]);
    expect(expiryChoices(policy(1, [1], 1))).toEqual([1]);
    expect(expiryChoices(policy(30, undefined, 7))).toEqual([]);
    expect(expiryChoices(undefined)).toEqual([]);
    expect(defaultExpiry(policy(30, [1, 7, 30], 7))).toBe(7);
    expect(defaultExpiry(policy(5, [1, 5], 5))).toBe(5);
    expect(defaultExpiry(policy(5, [1, 5], 7))).toBe(5);
    expect(defaultExpiry(policy(30, [], 7))).toBeNull();
    expect([1, 7].map(expiryLabel)).toEqual(["1 day", "7 days"]);
  });

  it("a link the policy holds: paused while off; capped by a lowered maximum", () => {
    expect(pausedLine(link, "Support Agents")).toMatch(
      /^This link doesn't open while public links are off for Support Agents\. Turning them on reopens it\. Expires \w{3} 29 Oct\.$/,
    );
    expect(
      anyoneLine({ ...link, expires_at_requested: "2026-11-05T12:00:00Z" }),
    ).toMatch(/Expires \w{3} 29 Oct \(the workspace's longest\)\.$/);
    expect(
      earlierLine({ ...link, revoked_at: "2026-10-23T11:00:00Z" }),
    ).toMatch(/ · not opened$/);
  });

  it("link lines", () => {
    expect(publicUrl("https://app.example", link)).toBe(
      "https://app.example/shared/abc",
    );
    expect(anyoneLine(link)).toMatch(
      /^Anyone with the link can see the .* without signing in\. Expires \w{3} 29 Oct\.$/,
    );
    expect(
      anyoneLine({
        ...link,
        version: "frozen",
        version_number: 6,
        include: { rows: true, experiments: true },
      }),
    ).toMatch(
      /^Anyone with the link can see the rows and experiments of v6, without signing in\. Expires \w{3} 29 Oct\.$/,
    );
    expect(
      anyoneLine({
        ...link,
        version: "latest",
        include: { rows: true, experiments: false, feedback: true },
      }),
    ).toMatch(
      /^Anyone with the link can see the rows and human feedback of the latest version, without/,
    );
    expect(viewsLine(link, NOW)).toBe("Not opened yet");
    expect(
      viewsLine(
        { ...link, views: 1, last_opened_at: "2026-10-22T11:58:00Z" },
        NOW,
      ),
    ).toBe("Opened once · last 2 min ago");
    expect(
      viewsLine(
        { ...link, views: 3, last_opened_at: "2026-10-22T10:00:00Z" },
        NOW,
      ),
    ).toBe("Opened 3 times · last 2 h ago");
    expect(includeLabels(link.include)).toEqual([
      "Rows",
      "Experiments and run results",
    ]);
    expect(versionLabel(link)).toBe("v6, frozen");
    expect(versionLabel({ ...link, version: "latest" })).toBe(
      "Always the latest version",
    );
    expect(isActive(link, NOW)).toBe(true);
    expect(isActive({ ...link, revoked_at: "2026-10-22T11:00:00Z" }, NOW)).toBe(
      false,
    );
    expect(isActive(link, Date.parse("2026-10-30T00:00:00Z"))).toBe(false);
    expect(
      earlierLine({ ...link, revoked_at: "2026-10-23T11:00:00Z", views: 2 }),
    ).toMatch(
      /^v6, frozen · Rows, Experiments and run results · Turned off \w{3} 23 Oct · opened 2×$/,
    );
  });
});
