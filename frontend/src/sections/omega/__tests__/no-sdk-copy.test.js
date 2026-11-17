import { describe, expect, it } from "vitest";

import { readCode, relative, sources, sweep } from "./copy-sweep";

// Parity P22, the SDK switch's off state on the tree itself. No SDK package is
// published yet (Python `futureagi`, TypeScript `@future-agi/sdk`), so no
// Sandboxes or Deployments page may name an SDK, a client call or a package
// install unless that file reads the switch the release declares at publish
// (OMEGA_PART.SDK_PYTHON / SDK_TYPESCRIPT, shell/switches.js codeTabs). The
// REST curl panels (P11) are allowed: curl is not an SDK.

// code/ holds the panels both tabs open (P11): its SDK tabs sit behind the
// same switch.
const SCOPES = ["sandboxes/", "deployments/", "code/"];

const SDK_WORDS = [
  /\bSDKs?\b/,
  /@future-agi\/sdk\b/,
  /\bpip3?\s+install\b/,
  /\b(?:npm|yarn|pnpm)\s+(?:i|install|add)\b/,
  /\bfrom\s+futureagi\s+import\b/,
  /\bclient\.(?:sandboxes|deployments|runs)\b/,
];

const GATES = ["OMEGA_PART.SDK_PYTHON", "OMEGA_PART.SDK_TYPESCRIPT"];

// File -> the number of SDK-naming hits it holds, each checked to sit behind
// a gate. P11's SDK tabs: sdkSnippets.js returns null for a tab whose release
// part (OMEGA_PART.SDK_PYTHON / SDK_TYPESCRIPT) the server has not reported.
const GATED = { "code/sdkSnippets.js": 24 };

const files = sources().filter((file) =>
  SCOPES.some((scope) => relative(file).startsWith(scope)),
);

describe("No Sandboxes or Deployments page names an unpublished SDK", () => {
  it("reads the two tabs' sources", () => {
    expect(files.length).toBeGreaterThan(40);
  });

  it("names an SDK only in a listed file that reads the publish switch", () => {
    const hits = sweep(files, SDK_WORDS);
    const stray = hits
      .filter((hit) => !GATED[hit.file])
      .map((hit) => `${hit.file}:${hit.line}: ${hit.text}`);
    expect(stray).toEqual([]);
    for (const [file, lines] of Object.entries(GATED)) {
      const code = readCode(files.find((one) => relative(one) === file));
      expect(
        GATES.some((gate) => code.includes(gate)),
        `${file} reads an SDK switch`,
      ).toBe(true);
      expect(hits.filter((hit) => hit.file === file)).toHaveLength(lines);
    }
  });

  it("would catch the words it guards", () => {
    const planted = [
      "pip install futureagi",
      "npm install @future-agi/sdk",
      "Use the Python SDK",
      "from futureagi import Client",
      "await client.sandboxes.create()",
    ];
    for (const text of planted) {
      expect(
        SDK_WORDS.some((pattern) => pattern.test(text)),
        text,
      ).toBe(true);
    }
    expect(SDK_WORDS.some((pattern) => pattern.test("curl -s -X POST"))).toBe(
      false,
    );
  });
});
