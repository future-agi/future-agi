import { describe, expect, it } from "vitest";

import {
  MODERATION_CONSEQUENCES,
  MODERATION_NOTE_FIELDS,
} from "../hub/constants";
import { readCode, relative, sources, sweep } from "./copy-sweep";

// HIDE-UNFINISHED-FA, fix round 2. Nothing can install a Hub entry until a
// CLI with `omega hub` ships (OMEGA_PART HUB_CLI), and nothing counts an open
// until a "Use this" handoff ships, so every installs or opens count reads
// zero. Round 1 hid the entry page's always-empty panel and left the same
// metric on the cards, the publisher pages, the "Most installed" sort and the
// notices: the class again, so this sweep holds every Hub source to one gate
// (hub/lib/installs.js). A Hub file whose copy says "install" or "opened in"
// must read the gate, and the number of such lines in it is pinned, so a new
// line in a gated file is looked at rather than passed because the file
// reads its gate somewhere.

const HUB = "hub/";

// A word in copy, not a key: a string literal that is only the word
// ("installs", a sort value or a prop) and an identifier (`installs30d`) are
// left out; "Most installed" and "Installs, last 30 days" are copy.
const INSTALL_WORDS = [
  /(?<![\w/.-])(?!(?<=["'`])install(?:s|ed|able|ing)?["'`])install(?:s|ed|able|ing)?(?![\w/-])/i,
  /\bopened\s+in\b/i,
];

const GATE =
  /useInstallPath\(|showsInstalls\(|showsOpens\(|OMEGA_PART\.HUB_CLI|flags\.cli === true/;

// File -> lines that name installs or opens, each behind the file's gate
// (checked by hand and by the page tests in hub/__tests__/installsCopy and
// cliCopy). Change a count only after checking the new line is gated.
const GATED = {
  "hub/HubPublisherPage.jsx": 1,
  "hub/HubPublishersPage.jsx": 2,
  "hub/components/EntryCard.jsx": 1,
  "hub/components/InstallsStat.jsx": 3,
  "hub/components/MoveDialog.jsx": 1,
  "hub/components/Notices.jsx": 2,
  "hub/components/Tombstone.jsx": 1,
  "hub/components/UseThisMenu.jsx": 1,
  "hub/lib/useThis.js": 1,
};

// Not shown as written: the sort list lib/installs.js filters (sortsFor drops
// "Most installed" while nothing installs; checked below).
const FILTERED = { "hub/constants.js": 1 };

// A maintainer tool, not an offer: the blocklist page says what a block
// means for the clients that read the published list.
const MAINTAINER = {
  "hub/HubBlocklistPage.jsx":
    "defines the published blocklist (maintainers only)",
};

// Outside the Hub (round 2, the class again: Settings > Early access called
// the Hub a place to "Find, install and publish"), install copy is allowed
// only where it is about something real, each file pinned to its lines. A
// sandbox image's own tools are real: the template lists what it installs.
// "opened in" stays a Hub pattern (the opens count); outside the Hub it is
// ordinary prose ("can't be opened in this workspace").
// Studio's "Start omega dev" (STUDIO-INSTALL, integration round 4) installs the
// Omega CLI: the real `npm install -g <spec>` this deployment configures
// (release-config.js), or, with none configured, says there is no command to
// show and how to get the CLI. Since DEMO-FIX-2 item 5 there is no install step
// at all until the release names the package: StartOmegaDev.jsx's two more
// lines are the `install` value deciding that (`install ? 2 : 1`,
// `{install && (`), code, not copy, and StudioInstallStep.jsx and studio-copy.js
// each lost the two lines that said there was no command to show.
// The code panels' Python tab (P11) shows `pip install futureagi` only once
// the release reports the package published (OMEGA_PART.SDK_PYTHON; the SDK
// sweep no-sdk-copy.test.js holds the file to that gate).
const OUTSIDE_HUB = {
  "code/sdkSnippets.js": 1,
  "sandboxes/create/CreateSandboxForm.jsx": 1,
  "sandboxes/create/TemplateGallery.jsx": 1,
  "sandboxes/settings/SandboxSettingsPage.jsx": 1,
  "studio/StartOmegaDev.jsx": 4,
  "studio/StudioInstallStep.jsx": 3,
  "studio/release-config.js": 1,
  "studio/studio-copy.js": 2,
};

// Hub UX round 4: this exact moderation consequence explains the default
// resolution change, not an installation offer or a usage count. The helper
// describes public downloads and publisher email, not notification of installers.
// Audit only this line; the filtered sort count and every other gate stay pinned.
const YANK_REASON_HELP =
  "Shown publicly on the entry page and on downloads of this version, and emailed to the publisher.";
const YANK_CONSEQUENCE =
  "This version is marked as yanked and excluded from latest and new installs by default. It remains available by its exact version. Your reason is shown publicly and emailed to the publisher.";
const isYankConsequence = (hit) =>
  hit.file === "hub/constants.js" &&
  hit.text.trim() === `"${YANK_CONSEQUENCE}",`;

const files = sources().filter((file) => relative(file).startsWith(HUB));
const rawHits = sweep(files, INSTALL_WORDS);
const hits = rawHits.filter((hit) => !isYankConsequence(hit));
const others = sources().filter((file) => !relative(file).startsWith(HUB));
const otherHits = sweep(others, [INSTALL_WORDS[0]]);
const countBy = (list) =>
  list.reduce(
    (acc, hit) => ({ ...acc, [hit.file]: (acc[hit.file] || 0) + 1 }),
    {},
  );

describe("No Hub copy names installs or opens nothing can count", () => {
  it("reads the Hub sources", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("names them only in a listed file", () => {
    const stray = hits
      .filter(
        (hit) =>
          !GATED[hit.file] && !FILTERED[hit.file] && !MAINTAINER[hit.file],
      )
      .map((hit) => `${hit.file}:${hit.line}: ${hit.text}`);
    expect(stray).toEqual([]);
  });

  it("each listed file reads the gate", () => {
    for (const file of Object.keys(GATED)) {
      const code = readCode(files.find((f) => relative(f) === file));
      expect(code, file).toMatch(GATE);
    }
  });

  it("the sort list is filtered by the gate", () => {
    const code = readCode(
      files.find((f) => relative(f) === "hub/lib/installs.js"),
    );
    expect(code).toMatch(/useOmegaPart\(OMEGA_PART\.HUB_CLI\)/);
    expect(code).toContain('sort.value !== "installs"');
  });

  it("audits the exact yank consequence and helper without exempting other install copy", () => {
    expect(MODERATION_NOTE_FIELDS.yank_version.helperText).toBe(
      YANK_REASON_HELP,
    );
    expect(MODERATION_CONSEQUENCES.yank_version).toBe(YANK_CONSEQUENCE);
    expect(
      INSTALL_WORDS.some((pattern) => pattern.test(YANK_REASON_HELP)),
    ).toBe(false);
    expect(rawHits.filter(isYankConsequence)).toHaveLength(1);
    const target = "hub/constants.js";
    const planted = 'const ungatedCopy = "Install more Hub entries";';
    const extra = sweep(files, INSTALL_WORDS, (source, file) =>
      relative(file) === target ? `${source}\n${planted}\n` : source,
    ).filter((hit) => hit.file === target && !isYankConsequence(hit));
    expect(extra.some((hit) => hit.text === planted)).toBe(true);
    expect(() => expect(extra).toHaveLength(FILTERED[target])).toThrow();
  });

  it("each listed file has exactly the lines it was checked with", () => {
    const counted = countBy(hits);
    for (const [file, count] of Object.entries({ ...GATED, ...FILTERED })) {
      expect(
        { file, lines: counted[file] || 0 },
        hits
          .filter((hit) => hit.file === file)
          .map((hit) => `${hit.line}: ${hit.text}`)
          .join("\n"),
      ).toEqual({ file, lines: count });
    }
  });

  it("would catch the copy this round gated, and not keys", () => {
    for (const sample of [
      "Installs, last 30 days",
      "Most installed",
      "It still installs and is still found by search.",
      "It still installs by its exact version.",
      "Its versions can no longer be installed.",
      "Who publishes on the Hub, by installs over the last 30 days.",
      "${countOrDash(p.installs30d)} installs (30 days)",
      "Likes, installs, picks and versions move with",
      "Opened in Future AGI, last 30 days",
      'label="Installs, last 30 days"',
      '{ value: "installs", label: "Most installed" }',
    ]) {
      expect(
        INSTALL_WORDS.some((pattern) => pattern.test(sample)),
        sample,
      ).toBe(true);
    }
    for (const key of [
      'sort: "installs"',
      "entry.installs30d",
      'kind="installs"',
      "installsDaily30",
      'oneOf(["installs", "opens", "likes"])',
    ]) {
      expect(
        INSTALL_WORDS.some((pattern) => pattern.test(key)),
        key,
      ).toBe(false);
    }
  });
});

describe("Outside the Hub, install copy only where something really installs", () => {
  it("reads the other Omega sources, the Early access page among them", () => {
    expect(others.length).toBeGreaterThan(100);
    expect(others.map(relative)).toContain("shell/EarlyAccessPage.jsx");
  });

  it("names installs only in a listed file, with exactly its checked lines", () => {
    const stray = otherHits
      .filter((hit) => !OUTSIDE_HUB[hit.file])
      .map((hit) => `${hit.file}:${hit.line}: ${hit.text}`);
    expect(stray).toEqual([]);
    const counted = countBy(otherHits);
    for (const [file, count] of Object.entries(OUTSIDE_HUB)) {
      expect({ file, lines: counted[file] || 0 }).toEqual({
        file,
        lines: count,
      });
    }
  });

  it("would catch the Early access sentence this round removed", () => {
    expect(
      INSTALL_WORDS[0].test("Find, install and publish agents and skills."),
    ).toBe(true);
  });
});
