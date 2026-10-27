/*
  "Check what we found" (RSI PRD Flow 1, step 3, and "What we need"): what
  reading a code agent found — entry point, prompts, tools, model, how to
  run it and its tests — for the person to confirm or correct, plus what only
  they can say:
  prompts built at run time, tools that must never run for real, and the test
  credentials to use (a secret-manager reference, never a value).

  Corrections are kept on the agent's values (`foundCorrections`) and read
  back through definitionOf, so every surface shows the corrected record;
  the tests go to `repoTests`, the command every code change must keep
  passing. `foundCheckedAt` marks the item done — until then a newly
  connected code agent runs sealed on what we read, but not live, and a code
  Fix waits (needsFoundCheck).
*/
import {
  agentDefinitionOf,
  definitionOf,
  needsFoundCheck,
  sourceKindOf,
} from "../_mock/agentRegistry";
import { repoChecksOf } from "../_mock/connectAgent";

export { needsFoundCheck };

/** Code we read — a repository, an upload or a framework adapter. */
export const isCodeAgent = (agent) =>
  ["repo", "upload", "framework"].includes(sourceKindOf(agent));

/* The facts a person confirms or corrects, in the PRD's order. */
export const FOUND_FACTS = [
  { id: "entry", label: "Entry point", mono: true },
  { id: "prompts", label: "Prompts", mono: true, list: true },
  { id: "tools", label: "Tools", list: true, chips: true },
  { id: "model", label: "Model", mono: true },
  { id: "runCommand", label: "How to run it", mono: true },
  /* The repository's own test command — every code change keeps it passing. */
  { id: "tests", label: "Tests", mono: true },
];

/*
  The test command and the other checks it runs: as recorded at connect
  (or corrected here), else what reading the code finds.
*/
const testsOf = (agent, d, toolNames) => {
  const saved = agent?.values?.repoTests;
  const read = repoChecksOf({
    repo: d.repoUrl || agent?.name || "",
    folder: d.folder || "",
    tools: toolNames,
  }).tests;
  if (typeof saved === "string") return { command: saved, checks: read.checks };
  return {
    command: saved?.command || read.value,
    checks: saved?.checks || read.checks,
  };
};

/** { entry, prompts, tools, model, runCommand, tests, testChecks, runtimePrompts, modelAtRuntime, credentialKeys } as read (corrections applied). */
export const foundValuesOf = (agent) => {
  const d = definitionOf(agent);
  const read = agentDefinitionOf({ agent });
  const toolNames = (d.tools || read.tools?.items || [])
    .map((t) => (typeof t === "string" ? t : t.name))
    .filter(Boolean);
  const tests = testsOf(agent, d, toolNames);
  return {
    tests: tests.command,
    testChecks: tests.checks,
    entry: d.entry || "",
    prompts: d.prompts || [],
    tools: toolNames,
    model: d.model || (read.model?.visible ? read.model.name : "") || "",
    runCommand: d.runCommand || d.installCommand || "",
    runtimePrompts: d.runtimePrompts || [],
    modelAtRuntime: !!d.modelAtRuntime,
    credentialKeys: (d.credentialKeys || [])
      .map((k) => (typeof k === "string" ? k : k.name))
      .filter(Boolean),
  };
};

/**
 * The values patch a review saves: only what was corrected goes into
 * `foundCorrections` (tools keep their descriptions where the name stayed).
 */
export const foundPatchOf = (
  agent,
  { corrected, runtimePrompts, neverRunForReal, testCredentialsRef },
) => {
  const d = definitionOf(agent);
  const known = Object.fromEntries(
    (d.tools || [])
      .filter((t) => typeof t === "object")
      .map((t) => [t.name, t]),
  );
  const fixes = { ...(agent?.values?.foundCorrections || {}) };
  const { tests: testsFix, ...defined } = corrected;
  Object.entries(defined).forEach(([id, value]) => {
    fixes[id] =
      id === "tools"
        ? value.map((name) => known[name] || { name, desc: "", args: [] })
        : value;
  });
  if (runtimePrompts?.length) fixes.runtimePrompts = runtimePrompts;
  /* Confirmed or corrected, the tests go on the record as what every code
     change must keep passing. */
  const found = foundValuesOf(agent);
  return {
    repoTests: {
      command: (testsFix ?? found.tests) || "",
      checks: found.testChecks,
    },
    foundCorrections: fixes,
    neverRunForReal,
    testCredentialsRef: testCredentialsRef || "",
    foundCheckedAt: new Date().toISOString(),
  };
};
