import { execFileSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  MAX_BUNDLE_BYTES,
  PROJECT_MANIFEST,
  gunzip,
  readManifest,
} from "../lib/bundle";
import {
  SAMPLE_FILES,
  sampleArchive,
  sampleBundle,
} from "../lib/sampleProject";
import { readTarMembers } from "../lib/tar";

// The sample project (parity gap 8, the W0 floor): a project with no model key
// and no network that deploys as it is. These checks run without a stack; the
// live deploy of these exact bytes is the rig walk's (.omega/program/HIDE-SWITCHES).

// The runtime provides @omega/core. Here the entry's own TypeScript is
// stripped of its types by Vite's esbuild (in a plain Node process: esbuild
// refuses jsdom's typed arrays) and run against a stand-in that records what
// the module builds, so the tool's logic runs exactly as written.
const FRONTEND = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../../..",
);
const TRANSPILE = [
  'const { transformWithEsbuild } = await import("vite");',
  "let source = '';",
  "for await (const chunk of process.stdin) source += chunk;",
  'const out = await transformWithEsbuild(source, "triage.ts", { loader: "ts", format: "cjs", target: "es2020" });',
  "process.stdout.write(out.code);",
].join("\n");

const omegaCore = {
  createOmega: (options) => ({ kind: "omega", options }),
  defineWorkflow: (spec) => ({ kind: "workflow", ...spec }),
  InMemoryTraceSink: class InMemoryTraceSink {},
  inputStep: (id) => ({ kind: "input", id }),
  outputStep: (id) => ({ kind: "output", id }),
  tool: (spec) => spec,
  toolStep: (id, toolLike) => ({ kind: "tool", id, tool: toolLike.name }),
};

async function loadEntry() {
  const code = execFileSync(
    process.execPath,
    ["--input-type=module", "-e", TRANSPILE],
    { cwd: FRONTEND, input: text("entries/triage.ts"), encoding: "utf8" },
  );
  const module = { exports: {} };
  const requireStub = (name) => {
    if (name !== "@omega/core") throw new Error(`unexpected import ${name}`);
    return omegaCore;
  };
  const run = vm.runInThisContext(
    `(function (require, module, exports) {\n${code}\n})`,
    { filename: "entries/triage.ts" },
  );
  run(requireStub, module, module.exports);
  return module.exports;
}

const text = (path) => SAMPLE_FILES.find((file) => file.path === path).text;
const decoder = new TextDecoder();

describe("the sample's files", () => {
  it("are a manifest at the root, a README and the one entry module, nothing else", () => {
    expect(SAMPLE_FILES.map((file) => file.path)).toEqual([
      "README.md",
      "entries/triage.ts",
      PROJECT_MANIFEST,
    ]);
    const manifest = JSON.parse(text(PROJECT_MANIFEST));
    expect(manifest.name).toBe("support-triage");
    expect(manifest.entries).toHaveLength(1);
    const [entry] = manifest.entries;
    expect(entry).toMatchObject({
      id: "triage",
      kind: "workflow",
      module: "./entries/triage.ts",
      export: "workflow",
    });
    expect(SAMPLE_FILES.some((file) => `./${file.path}` === entry.module)).toBe(
      true,
    );
  });

  it("import nothing but @omega/core, which the runtime provides, and need no key", () => {
    const source = text("entries/triage.ts");
    const imports = [...source.matchAll(/from\s+"([^"]+)"/g)].map((m) => m[1]);
    expect(imports).toEqual(["@omega/core"]);
    expect(source).not.toMatch(/process\.env|fetch\(|apiKey|API_KEY/);
    expect(SAMPLE_FILES.some((file) => file.path === "package.json")).toBe(
      false,
    );
  });

  it("README: no unreleased command, nothing in progress, and the call it documents", () => {
    const readme = text("README.md");
    expect(readme).not.toMatch(/\bomega\s+(?:deploy|hub)\b|\bCLI\b/);
    expect(readme).not.toMatch(/coming soon|\bpreview\b|\bbeta\b/i);
    expect(readme).toContain('"$DEPLOYMENT_URL/studio/v1/runs"');
    expect(readme).toContain('"entry": "triage"');
    expect(readme).toContain("omega-sample-support-triage.tar.gz");
  });

  it("README: says where the call must run while the URL is host-only, and that the answer sits under content", () => {
    const readme = text("README.md").replace(/\s+/g, " ");
    // DEMO-FIX-2's host-only note: "Only the machine that runs Omega can reach this address."
    expect(readme).toContain(
      "If the page says only the machine that runs Omega can reach that address, run the call on that machine",
    );
    expect(readme).toContain(
      "The run's output is the `triage_message` tool's result. Its `content` holds the answer",
    );
    expect(readme).not.toMatch(/In the run's output, `team`/);
  });
});

describe("the entry's logic", () => {
  it("answers the README's example as billing, high", async () => {
    const { triage, triageMessage, workflow } = await loadEntry();
    const readme = text("README.md");
    const example = /"message": "([^"]+)"/.exec(readme)[1];
    expect(triage(example)).toEqual({
      team: "billing",
      priority: "high",
      matched: ["refund", "charged", "urgent"],
    });
    // The README names the tool, the content fields and the matched words the
    // entry really returns (the tool's result is {name, ok, content} on the
    // real core: fix round 1, verifier finding 4a).
    expect(triageMessage.name).toBe("triage_message");
    const flat = readme.replace(/\s+/g, " ");
    expect(flat).toContain(
      `\`team\` is \`${triage(example).team}\`, \`priority\` is \`${triage(example).priority}\``,
    );
    expect(flat).toContain(
      `(${triage(example)
        .matched.map((word) => `\`${word}\``)
        .join(", ")})`,
    );
    // The tool step reads the run's input as sent: an object, or a plain string.
    await expect(triageMessage.execute({ message: example })).resolves.toEqual(
      triage(example),
    );
    await expect(
      triageMessage.execute({ input: "my login is locked out" }),
    ).resolves.toMatchObject({ team: "access", priority: "normal" });
    expect(workflow.steps.map((step) => step.kind)).toEqual([
      "input",
      "tool",
      "output",
    ]);
  });

  it("matches whole words only, and falls back to general", async () => {
    const { triage } = await loadEntry();
    expect(triage("How do I download my invoice?")).toEqual({
      team: "billing",
      priority: "normal",
      matched: ["invoice"],
    });
    expect(triage("The site is DOWN, error 500")).toMatchObject({
      team: "bugs",
      priority: "high",
    });
    expect(triage("Hello there")).toEqual({
      team: "general",
      priority: "normal",
      matched: [],
    });
    expect(triage("")).toMatchObject({ team: "general" });
  });
});

describe("the sample bundle", () => {
  it("is the same tar every time, with the files' exact bytes at the root", async () => {
    const [first, second] = await Promise.all([sampleBundle(), sampleBundle()]);
    expect(first.contentSha256).toBe(second.contentSha256);
    expect(first.source).toBe("archive");
    expect(first.size).toBeLessThan(MAX_BUNDLE_BYTES);
    expect(first.sha256).toMatch(/^[0-9a-f]{64}$/);
    const members = readTarMembers(await gunzip(await sampleArchive()));
    expect(members.map((member) => member.path)).toEqual(
      SAMPLE_FILES.map((file) => file.path),
    );
    for (const member of members) {
      expect(decoder.decode(member.data)).toBe(text(member.path));
    }
  });

  it("passes the page's own checks, as a picked .tar.gz does", async () => {
    const bundle = await sampleBundle();
    expect(bundle.manifest).toEqual(
      readManifest(new TextEncoder().encode(text(PROJECT_MANIFEST))),
    );
    expect(bundle.manifest.entries).toEqual([
      { id: "triage", label: "Triage a support message", kind: "workflow" },
    ]);
    expect(bundle.excluded).toEqual([]);
    expect(bundle.files.map((file) => file.path)).toContain(PROJECT_MANIFEST);
  });
});
