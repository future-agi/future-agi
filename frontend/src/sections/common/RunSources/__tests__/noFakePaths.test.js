// dataset integration FE-0 (BUILD-BRIEF §C.1; owner: "i need no dummy thing", Q12, Q14,
// and "3rd party ... platforms like vapi" are out): the drawer, the
// connect code, the experiment pages, the run views, the compare page, the
// MSW world and the examples carry no fake path. It reads the files
// themselves, so a string in dead code fails it too.
//
// Two tiers:
//  - DRAWER scopes (the brief's list): every EXACT string and every
//    platform name, ElevenLabs included (its TTS key is the one exception).
//    FE-1 adds the generated-runner names here once snippets/sdk.js
//    replaces the generators that still live under RunSources.
//  - WIDE scopes (review must-fix 1 and 3): the EXACT strings, the
//    generated-runner names, and the hosted voice platforms. ElevenLabs is
//    a TTS vendor there (TTS runs, voices), so only "ElevenLabs Agents"
//    counts. Simulate's own saved agents (a separate product, DESIGN §4.6)
//    are the one recorded exception.
import { describe, expect, it } from "vitest";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// <frontend>/src/sections/common/RunSources/__tests__ → <frontend>
const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "../../../../..");
const ROOT = resolve(FRONTEND, "..");

const DRAWER_SCOPES = [
  "src/sections/common/RunSources",
  "src/sections/common/Connect",
  "src/sections/develop-detail/Experiment",
  "../examples/experiments",
];
const WIDE_SCOPES = [
  "src/_mock/api",
  "src/sections/common/Runs",
  "src/sections/experiment-detail",
  "src/sections/task-compare",
];
const TEXT = /\.(js|jsx|ts|tsx|mjs|py|md|json|ya?ml|sh|txt|toml)$/;

/** Exact strings (case-sensitive): commands and stand-ins that do not exist. */
const EXACT = [
  "fi_connect_shim",
  "fake_cli",
  "futureagi connect",
  "futureagi 0.9",
  "futureagi 0.7",
  "futureagi[connect]",
  "@futureagi/connect",
  "npx futureagi",
  "fi.connect",
  "connect.futureagi.com",
  "--depth",
  "REPLAY_CALLS",
  "voiceEstimate",
];
/** Generated runner scripts that never existed as a real path (WIDE). */
const RUNNERS = [
  "run_and_upload",
  "run_harness_and_upload",
  "sandbox_server",
  "serve_agent",
  "upload_results",
];
/** Third-party voice platforms (case-insensitive). */
const PLATFORMS = ["vapi", "retell", "bland"];
const ELEVENLABS_ANY = /elevenlabs/i;
const ELEVENLABS_AGENTS = /elevenlabs[\s_-]*agents?/i;

/** ElevenLabs TTS is a model vendor, not the Agents platform. */
const DRAWER_ALLOWED = [/ELEVENLABS_API_KEY/g];
/**
 * Simulate's saved agents (its own product and contract) name the
 * platforms they run on; the experiment drawer does not offer them.
 */
const SIMULATE_FILES = new Set([
  "frontend/src/_mock/prototype/fixtures/world/agentDefinitions.js",
]);
const SIMULATE_ALLOWED = [
  /results: \[\{id, agent_name, agent_type: "voice", provider: "vapi"/g,
];

function filesUnder(dir) {
  let out = [];
  readdirSync(dir).forEach((name) => {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) {
      if (name === "__tests__" || name === "node_modules") return;
      out = out.concat(filesUnder(p));
    } else if (TEXT.test(name)) out.push(p);
  });
  return out;
}

const scopeFiles = (scopes) =>
  scopes.flatMap((s) => filesUnder(resolve(FRONTEND, s)));
const rel = (f) => relative(ROOT, f);
const strip = (text, patterns) =>
  patterns.reduce((t, re) => t.replace(re, ""), text);

describe("no fake paths (FE-0)", () => {
  it("every scope exists (a moved folder never shrinks the scan)", () => {
    [...DRAWER_SCOPES, ...WIDE_SCOPES].forEach((s) =>
      expect(existsSync(resolve(FRONTEND, s)), s).toBe(true),
    );
    expect(scopeFiles(DRAWER_SCOPES).length).toBeGreaterThan(50);
    expect(scopeFiles(WIDE_SCOPES).length).toBeGreaterThan(100);
    expect(existsSync(resolve(FRONTEND, "src/_mock/prototype"))).toBe(false);
  });

  it("names no command, package or stand-in that does not exist", () => {
    const hits = [];
    scopeFiles([...DRAWER_SCOPES, ...WIDE_SCOPES]).forEach((f) => {
      const text = readFileSync(f, "utf8");
      EXACT.forEach((s) => {
        if (text.includes(s)) hits.push(`${rel(f)}: ${s}`);
      });
    });
    expect(hits).toEqual([]);
  });

  it("prints no generated runner on the run, experiment, compare and mock pages", () => {
    const hits = [];
    scopeFiles(WIDE_SCOPES).forEach((f) => {
      const text = readFileSync(f, "utf8");
      RUNNERS.forEach((s) => {
        if (text.includes(s)) hits.push(`${rel(f)}: ${s}`);
      });
    });
    expect(hits).toEqual([]);
  });

  it("names no third-party voice platform in the drawer", () => {
    const hits = [];
    scopeFiles(DRAWER_SCOPES).forEach((f) => {
      const lower = strip(
        readFileSync(f, "utf8"),
        DRAWER_ALLOWED,
      ).toLowerCase();
      [...PLATFORMS, "elevenlabs"].forEach((s) => {
        if (lower.includes(s)) hits.push(`${rel(f)}: ${s}`);
      });
    });
    expect(hits).toEqual([]);
  });

  it("names no hosted voice platform on the run, experiment, compare and mock pages", () => {
    const hits = [];
    scopeFiles(WIDE_SCOPES).forEach((f) => {
      if (SIMULATE_FILES.has(rel(f))) return;
      const text = strip(readFileSync(f, "utf8"), SIMULATE_ALLOWED);
      const lower = text.toLowerCase();
      PLATFORMS.forEach((s) => {
        if (lower.includes(s)) hits.push(`${rel(f)}: ${s}`);
      });
      if (ELEVENLABS_AGENTS.test(text))
        hits.push(`${rel(f)}: ElevenLabs Agents`);
    });
    expect(hits).toEqual([]);
  });

  it("the guard itself fails on a planted string (positive control)", () => {
    expect("python run_and_upload.py".includes(RUNNERS[0])).toBe(true);
    expect(ELEVENLABS_AGENTS.test("ElevenLabs Agents")).toBe(true);
    expect(ELEVENLABS_AGENTS.test("ElevenLabs Rachel")).toBe(false);
    expect(ELEVENLABS_ANY.test("ELEVENLABS_API_KEY")).toBe(true);
  });
});
