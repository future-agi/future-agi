import { describe, expect, it } from "vitest";

import { readCode, relative, sources, sweep } from "./copy-sweep";

// HIDE-UNFINISHED-FA, fix round 1. No customer can install the Omega CLI yet
// (`omega hub` and `omega deploy` answer "Unknown command"), so no copy may
// name it unless the server says that part of the CLI is released
// (omega_rollout/readiness.py: OMEGA_PART HUB_CLI, DEPLOY_CLI). Round 0 hid
// four such controls one by one and missed four more (the Remixes command,
// the tombstone's `omega hub outdated`, the yanked notice's "the CLI warns",
// the Bundle file hint's `omega deploy bundle --out`). This sweep closes the
// class: every line that names the CLI must sit in a file listed below, and
// that file (or every file that imports it) must read the matching gate.
//
// Left out: studio/ and embed/. The Studio tab stays visible by the owner's
// ruling of 2026-10-19 (#4) and `omega dev` is its install path, which lane
// p-studio-install owns (the release publishes the CLI with it).

const SKIP_PREFIXES = ["studio/", "embed/"];

const CLI_WORDS = [
  /\bomega\s+(?:hub|deploy)\b/,
  /\bCLI\b/,
  /\b(?:your|the|a)\s+terminal\b/i,
  // Calls, imports and references: aliases must not make a producer invisible.
  /\bhubCommand\b/,
  /\[\s*["']omega["']\s*,\s*["']hub["']/,
];

const HUB_CLI = "OMEGA_PART.HUB_CLI";
const DEPLOY_CLI = "OMEGA_PART.DEPLOY_CLI";

// File -> how it is gated. `self`: text its own code must contain.
// `importers`: text every file that imports it must contain. `lines`: how
// many CLI-naming lines it holds, each checked by hand to sit behind the gate
// (and rendered with no part by hub/__tests__/cliCopy.test.jsx). Round 2: the
// count is pinned, so a new line in a listed file fails here and is looked at,
// instead of passing because the file reads its gate somewhere. The command
// refactor preserves every old command hit and adds one builder-import hit
// in each caller (4/2/2/4 -> 5/3/3/5), rather than lowering the old counts.
const GATED = {
  "deployments/new/SourceStep.jsx": { self: DEPLOY_CLI, lines: 5 },
  "hub/HubPublishPage.jsx": { self: HUB_CLI, lines: 5 },
  "hub/HubReviewQueuePage.jsx": { self: HUB_CLI, lines: 1 },
  "hub/HubSubmissionsPage.jsx": { self: HUB_CLI, lines: 1 },
  "hub/components/Notices.jsx": { self: HUB_CLI, lines: 1 },
  "hub/components/RemixBlock.jsx": { self: HUB_CLI, lines: 3 },
  "hub/components/Tombstone.jsx": { self: HUB_CLI, lines: 1 },
  // HUB-UX-05: TokenSettings.jsx:193 adds the revoke confirmation
  // "The CLI cannot use this token for new publishing requests." HubSettingsPage renders
  // TokenSettings only when cliReleased (OMEGA_PART.HUB_CLI).
  "hub/components/TokenSettings.jsx": { importers: HUB_CLI, lines: 4 },
  "hub/lib/useThis.js": {
    self: "flags.cli === true",
    importers: HUB_CLI,
    lines: 5,
  },
};

// A pure builder is not a rendered offer. Pin its declaration + split prefix,
// and audit every consumer below; never exempt a shared module wholesale.
const BUILDERS = { "hub/lib/registry.js": { lines: 2 } };
const CHECKED = { ...GATED, ...BUILDERS };
// The two URL/copy-classification imports do not produce commands. Exact
// bindings keep aliases, namespace imports and re-exports from bypassing the
// producer census; any new consumer needs an explicit gate review.
const REGISTRY_IMPORTS = {
  "hub/HubEntryPage.jsx": ["import{hubApiUrl}"],
  "hub/HubPublishPage.jsx": ["import{hubCommand}"],
  "hub/components/RegistryEmpty.jsx": ["import{isHostedHub}"],
  "hub/components/RemixBlock.jsx": ["import{hubCommand}"],
  "hub/components/TokenSettings.jsx": ["import{hubCommand}"],
  "hub/lib/useThis.js": ["import{hubCommand}"],
};

// Data, not an offer: the line shows only for a stored record that really
// came through the CLI. Each entry names the exact text it allows.
const DATA_LABELS = {
  "hub/HubSubmissionPage.jsx": {
    text: '"Omega CLI"',
    why: "names the channel a stored submission came in by",
  },
};

const files = sources().filter(
  (file) => !SKIP_PREFIXES.some((prefix) => relative(file).startsWith(prefix)),
);
const codeOf = new Map(files.map((file) => [relative(file), readCode(file)]));

function importersOf(target) {
  const base = target
    .split("/")
    .pop()
    .replace(/\.jsx?$/, "");
  const spec = new RegExp(`from ["'][^"']*/${base}["']`);
  return [...codeOf.entries()]
    .filter(([file, code]) => file !== target && spec.test(code))
    .map(([file]) => file);
}

describe("No copy names an unreleased Omega CLI", () => {
  it("reads the tab sources", () => {
    expect(files.length).toBeGreaterThan(100);
  });

  it("names the CLI only where the matching readiness gate is read", () => {
    const stray = sweep(files, CLI_WORDS)
      .filter((hit) => {
        if (CHECKED[hit.file]) return false;
        const label = DATA_LABELS[hit.file];
        return !(label && hit.text.includes(label.text) && hit.match === "CLI");
      })
      .map((hit) => `${hit.file}:${hit.line}: ${hit.text}`);
    expect(stray).toEqual([]);
  });

  it("explicitly audits the gated revoke confirmation and rejects an extra ungated CLI line", () => {
    const target = "hub/components/TokenSettings.jsx";
    expect(codeOf.get(target)).toContain(
      "The CLI cannot use this token for new publishing requests.",
    );
    const planted = 'const ungatedCopy = "Publish with the CLI";';
    const hits = sweep(files, CLI_WORDS, (source, file) =>
      relative(file) === target ? `${source}\n${planted}\n` : source,
    ).filter((hit) => hit.file === target);
    expect(hits.some((hit) => hit.text === planted)).toBe(true);
    // Run the census assertion itself: the newly approved fourth line is
    // not a wholesale exemption for a fifth, ungated line in the same file.
    expect(() => expect(hits).toHaveLength(GATED[target].lines)).toThrow();
  });

  it("each listed file really reads its gate", () => {
    for (const [file, gate] of Object.entries(GATED)) {
      const code = codeOf.get(file);
      expect(code, `${file} exists`).toBeTypeOf("string");
      if (gate.self) expect(code, file).toContain(gate.self);
      if (gate.importers) {
        const importers = importersOf(file);
        expect(importers.length, `${file} has an importer`).toBeGreaterThan(0);
        for (const importer of importers) {
          expect(codeOf.get(importer), `${importer} imports ${file}`).toContain(
            gate.importers,
          );
        }
      }
    }
  });

  it("audits every registry consumer and gates all command-producing imports", () => {
    const imports = {};
    for (const [file, code] of codeOf) {
      const matches = [
        ...code.matchAll(
          /\b(import|export)\s+([^;]*?)\s+from\s+["'][^"']*\/registry(?:\.jsx?)?["']/g,
        ),
      ];
      if (matches.length) {
        imports[file] = matches.map((match) =>
          `${match[1]}${match[2]}`.replace(/\s/g, ""),
        );
      }
    }
    expect(imports).toEqual(REGISTRY_IMPORTS);
    for (const [file, bindings] of Object.entries(imports)) {
      if (bindings.includes("import{hubCommand}")) {
        expect(GATED[file]?.self || GATED[file]?.importers, file).toBeTruthy();
      }
    }
  });

  it("each listed file has exactly the lines it was checked with", () => {
    const hits = sweep(files, CLI_WORDS);
    for (const [file, gate] of Object.entries(CHECKED)) {
      const mine = hits.filter((hit) => hit.file === file);
      expect(
        { file, lines: mine.length },
        mine.map((hit) => `${hit.line}: ${hit.text}`).join("\n"),
      ).toEqual({ file, lines: gate.lines });
    }
  });

  it("lists no file that no longer names the CLI", () => {
    const naming = new Set(sweep(files, CLI_WORDS).map((hit) => hit.file));
    for (const file of [...Object.keys(CHECKED), ...Object.keys(DATA_LABELS)]) {
      expect(naming.has(file), file).toBe(true);
    }
  });

  it.each([
    'const copy = hubCommand(["login"]);',
    'const copy = ["omega", "hub", "login"].join(" ");',
    'const copy = "omega hub login";',
  ])("detects a planted ungated command: %s", (planted) => {
    const target = "hub/HubBrowsePage.jsx";
    const hits = sweep(files, CLI_WORDS, (source, file) =>
      relative(file) === target ? `${source}\n${planted}\n` : source,
    );
    expect(
      hits.some((hit) => hit.file === target && hit.text === planted),
    ).toBe(true);
    expect(GATED[target]).toBeUndefined();
  });

  it("detects an extra builder call even inside an already listed file", () => {
    const target = "hub/HubPublishPage.jsx";
    const planted = 'const ungated = hubCommand(["status"]);';
    const hits = sweep(files, CLI_WORDS, (source, file) =>
      relative(file) === target ? `${source}\n${planted}\n` : source,
    );
    expect(hits.filter((hit) => hit.file === target)).toHaveLength(
      GATED[target].lines + 1,
    );
  });

  it("would catch the copy this round gated or reworded", () => {
    for (const sample of [
      "Remix it from your terminal:",
      "omega hub new my-first-agent --from hub:future-agi/first-agent@1.0.0",
      "Its versions can no longer be installed. `omega hub outdated` reports it in projects that use it.",
      "It still installs by its exact version; the CLI warns.",
      "It goes through the same checks and review as the CLI.",
      "A bundle written by omega deploy bundle --out. It is sent as it is.",
      "Left out, as <code>omega deploy</code> does:",
      "Publish from your terminal or upload a folder.",
    ]) {
      expect(
        CLI_WORDS.some((pattern) => pattern.test(sample)),
        sample,
      ).toBe(true);
    }
    for (const allowed of [
      'icon: "terminal",',
      "OMEGA_PART.HUB_CLI",
      "terminalEvents",
    ]) {
      expect(
        CLI_WORDS.some((pattern) => pattern.test(allowed)),
        allowed,
      ).toBe(false);
    }
  });
});
