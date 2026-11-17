import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DISCLOSURE } from "../deployments/components/DeploymentPreviewNotice";

import {
  ROOT,
  readCode,
  relative,
  sources,
  sweep,
  withoutRegexLiterals,
} from "./copy-sweep";

// HIDE-UNFINISHED-FA, fix round 1 (AUDIT.md 3.2 F13, F16; READINESS COPY1).
// shared/customerSafe.js keeps a server's own words off our pages when they
// carry operator text; this sweep holds our OWN copy to the same words. Round 0
// left "Container · binding colima-default", a "Binding <id>" tooltip and three
// "your operator" sentences in the Sandboxes tab. Identifiers such as
// `operator.images` or `provider.bindingId` are code, so the patterns match the
// words only in the shapes a sentence uses.

const SKIP_FILES = new Set(["shared/customerSafe.js"]);

const INTERNAL = [
  /\b(?:your|the|an?|this|deployment(?:'|&apos;)s)\s+operators?\b/i,
  /\bbindings?\s+\$\{/i,
  /colima/i,
  /container\s+host/i,
  /\bdaemon\b/i,
  /\bcontrol[- ]plane\b/i,
];

// The CLI's own `--control-plane` flag, in the "Use the CLI" source that shows
// only once `omega deploy` is released (OMEGA_PART DEPLOY_CLI).
const ALLOWED = {
  "deployments/new/SourceStep.jsx": "OMEGA_PART.DEPLOY_CLI",
};

// Decision (2026-10-31): E06 stays verbatim; COPY1 exempts only
// this exact disclosure, not the word or the whole file. E06 DISCLOSURE.txt
// sha256 f70d013b80e18a9fed1275bab140d4438aa50bc0e728aa37b8c079f2837ffa16.
// Import the UI's single source of truth so this allow entry cannot drift.
const ALLOWED_EXACT_COPY = new Map([
  [DISCLOSURE, "deployments/components/DeploymentPreviewNotice.jsx"],
]);

const files = sources().filter((file) => !SKIP_FILES.has(relative(file)));

/**
 * Mask only approved complete literals, before comment/regex stripping can
 * change their contents. A matching physical line inside a larger template is
 * not a StringLiteral node and therefore cannot acquire this exception.
 * @param {string} source
 * @param {string} file
 */
function maskExactCopy(source, file) {
  if (![...ALLOWED_EXACT_COPY.values()].includes(relative(file))) return source;
  const parsed = ts.createSourceFile(
    file,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.JSX,
  );
  let masked = source;
  /** @param {import("typescript").Node} node */
  function visit(node) {
    if (
      ts.isStringLiteral(node) &&
      ALLOWED_EXACT_COPY.get(node.text) === relative(file)
    ) {
      const start = node.getStart(parsed);
      const end = node.getEnd();
      const lineStart = source.lastIndexOf("\n", start - 1) + 1;
      const nextLine = source.indexOf("\n", end);
      const line = source.slice(
        lineStart,
        nextLine === -1 ? undefined : nextLine,
      );
      // Retain the existing standalone-line restriction, but never use it as
      // proof of literal boundaries: those come from the original source AST.
      if (line.trim() === `${JSON.stringify(node.text)};`) {
        masked =
          masked.slice(0, start) +
          source.slice(start, end).replace(/[^\r\n]/g, " ") +
          masked.slice(end);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(parsed);
  return masked;
}

function internalCopyHits(candidates) {
  return sweep(candidates, INTERNAL, maskExactCopy).filter(
    (hit) => !(ALLOWED[hit.file] && /control[- ]plane/i.test(hit.match)),
  );
}

// Independent acceptance fixture: the exception must not follow a changed word.
const E06_DISCLOSURE =
  "Self-hosted Deployments is a preview. Run it only on a dedicated deploy-engine host you own, not shared with other workloads or tenants. Until a separate control daemon ships, the server and its deploy engine are root-equivalent on that host. Each deployment's outbound network is isolated in its own network namespace, which is on by default.";
const NOTICE_FILE = "deployments/components/DeploymentPreviewNotice.jsx";

/** Exercise the same sweep/filter used for real files, with one source fixture. */
function fixtureHits(file, code) {
  const absolute = path.join(ROOT, file);
  const read = vi.spyOn(fs, "readFileSync").mockReturnValue(code);
  const hits = internalCopyHits([absolute]);
  expect(read).toHaveBeenCalledWith(absolute, "utf8");
  return hits;
}

afterEach(() => vi.restoreAllMocks());

describe("E06's exact-copy exception to COPY1", () => {
  it("finds zero COPY1 hits in the real notice file", () => {
    const notice = path.join(ROOT, NOTICE_FILE);
    expect(fs.readFileSync(notice, "utf8")).toContain(
      `\n  ${JSON.stringify(E06_DISCLOSURE)};\n`,
    );
    expect(internalCopyHits([notice])).toEqual([]);
  });

  it("allows the verbatim disclosure in the notice source", () => {
    expect(
      fixtureHits(
        NOTICE_FILE,
        `const DISCLOSURE =\n  ${JSON.stringify(E06_DISCLOSURE)};`,
      ),
    ).toEqual([]);
  });

  it.each([NOTICE_FILE, "deployments/DeploymentsListPage.jsx"])(
    "still rejects any other daemon string in %s",
    (file) => {
      expect(fixtureHits(file, 'const copy = "Restart the daemon.";')).toEqual([
        expect.objectContaining({ file, match: "daemon" }),
      ]);
    },
  );

  it("rejects a one-word variant of the disclosure", () => {
    const variant = E06_DISCLOSURE.replace("dedicated", "shared");
    expect(
      fixtureHits(
        NOTICE_FILE,
        `const DISCLOSURE =\n  ${JSON.stringify(variant)};`,
      ),
    ).toEqual([expect.objectContaining({ match: "daemon" })]);
  });

  it("does not exempt even the exact disclosure in another file", () => {
    expect(
      fixtureHits(
        "deployments/DeploymentsListPage.jsx",
        `const copy =\n  ${JSON.stringify(E06_DISCLOSURE)};`,
      ),
    ).toEqual([expect.objectContaining({ match: "daemon" })]);
  });

  it("still rejects another daemon string beside the exact disclosure", () => {
    expect(
      fixtureHits(
        NOTICE_FILE,
        `const DISCLOSURE =\n  ${JSON.stringify(E06_DISCLOSURE)};\nconst extra = "Another daemon.";`,
      ),
    ).toEqual([expect.objectContaining({ line: 3, match: "daemon" })]);
  });

  it("does not allow a string that merely contains the disclosure", () => {
    expect(
      fixtureHits(
        NOTICE_FILE,
        `const copy =\n  ${JSON.stringify(`${E06_DISCLOSURE} Another daemon.`)};`,
      ),
    ).toHaveLength(2);
  });

  it.each([
    `const extra = \`prefix\n${JSON.stringify(E06_DISCLOSURE)};\nsuffix\`;`,
    `const extra = \`\n${JSON.stringify(E06_DISCLOSURE)};\n\`;`,
    `const extra = \`prefix \${value}\n${JSON.stringify(E06_DISCLOSURE)};\nsuffix\`;`,
  ])("rejects the approved physical line inside a larger template: %s", (code) => {
    expect(fixtureHits(NOTICE_FILE, code)).toEqual([
      expect.objectContaining({ line: 2, match: "daemon" }),
    ]);
  });

  it("does not let an approved literal hide a larger template elsewhere", () => {
    expect(
      fixtureHits(
        NOTICE_FILE,
        `const DISCLOSURE =\n  ${JSON.stringify(E06_DISCLOSURE)};\nconst extra = \`prefix\n${JSON.stringify(E06_DISCLOSURE)};\nsuffix\`;`,
      ),
    ).toEqual([expect.objectContaining({ line: 4, match: "daemon" })]);
  });

  it("does not mask another daemon string on the same source line", () => {
    expect(
      fixtureHits(
        NOTICE_FILE,
        `${JSON.stringify(E06_DISCLOSURE)}; const extra = "Another daemon.";`,
      ),
    ).toHaveLength(2);
  });
});

describe("No internal words in the Omega tabs' own copy", () => {
  it("reads the tab sources", () => {
    expect(files.length).toBeGreaterThan(100);
  });

  it("finds no operator, binding, Colima, container host, daemon or control-plane wording", () => {
    const hits = internalCopyHits(files).map(
      (hit) => `${hit.file}:${hit.line}: ${hit.text}`,
    );
    expect(hits).toEqual([]);
  });

  it("allows the CLI flag only behind its readiness gate", () => {
    for (const [file, gate] of Object.entries(ALLOWED)) {
      const match = files.find((candidate) => relative(candidate) === file);
      expect(readCode(match)).toContain(gate);
    }
  });

  it("would catch the copy this round removed, and not the identifiers", () => {
    for (const sample of [
      "caption={`${runtimeLabel(provider.kind)} · binding ${provider.bindingId}`}",
      "<Tooltip title={`Binding ${provider.bindingId}`} arrow>",
      "What people in this workspace can create. Your deployment&apos;s\n        operator sets the outer limits; you can narrow them here.",
      'caption="Images your operator allows outside the template catalog."',
      ': "Your deployment\'s operator offers sandboxes without a network only."}',
      "Container · binding colima-default",
      "The sandbox can reach the internet through the container host.",
    ]) {
      expect(
        INTERNAL.some((pattern) => pattern.test(sample)),
        sample,
      ).toBe(true);
    }
    // A log filter's regex literal is a pattern, not copy.
    expect(
      withoutRegexLiterals(
        "  /^\\s*Sending build context to Docker daemon\\b/i,",
      ),
    ).toBe("  /RE/,");
    for (const allowed of [
      "const { operator, caps, canEdit } = view;",
      "{operator.images.length > 0 && (",
      "bindingId: PropTypes.string,",
      "Up to ${operator.maxActivePerWorkspace} on this deployment.",
    ]) {
      expect(
        INTERNAL.some((pattern) => pattern.test(allowed)),
        allowed,
      ).toBe(false);
    }
  });
});
