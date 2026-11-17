// An engineering team. A verified task tree splits a feature into one subgoal per helper; an engineer agent
// writes each helper; CI (a real `node` run of the helper's check) decides whether it counts; verified work
// stays on disk, so a killed run resumes. With no key scripted models play the engineers.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { FileVerifiedTreeMemory, runAgent, runVerifiedTaskTree } from "@omega/core";
import { isLive, liveOrScripted } from "@omega/core/testing";

export const REQUEST = "Share cards: implement the helpers slugify, truncate and readingTime so the checks pass";
export const HELPERS = ["slugify", "truncate", "readingTime"] as const;
type Helper = (typeof HELPERS)[number];

/** The maintainers' checks: one script per helper. They are run by Node, not judged by a model. */
const CHECKS: Record<Helper, string> = {
  slugify: 'import { slugify } from "./src/slugify.mjs"; if (slugify("Hello, World!") !== "hello-world") process.exit(1); console.log("slugify ok");',
  truncate: 'import { truncate } from "./src/truncate.mjs"; if (truncate("abcdefghij", 6) !== "abcde…") process.exit(1); console.log("truncate ok");',
  readingTime: 'import { readingTime } from "./src/readingTime.mjs"; if (readingTime("word ".repeat(400)) !== 2) process.exit(1); console.log("readingTime ok");'
};
const CODE: Record<Helper, string> = {
  slugify: 'export const slugify = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");',
  truncate: 'export const truncate = (text, max) => (text.length <= max ? text : `${text.slice(0, max - 1)}…`);',
  readingTime: "export const readingTime = (text) => Math.max(1, Math.ceil(text.trim().split(/\\s+/).length / 200));"
};
/** What each check expects, said in the goal so a live engineer is not left to guess the return type. */
export const CONTRACT: Record<Helper, string> = {
  slugify: "slugify(text) returns the lower-case text with every run of other characters turned into one hyphen, no hyphen at either end",
  truncate: "truncate(text, max) returns text unchanged when it fits in max characters, else the first max-1 characters plus the single character …",
  readingTime: "readingTime(text) returns a plain number: the whole minutes to read at 200 words a minute, rounded up, at least 1"
};
const helperOf = (goal: string): Helper | undefined => HELPERS.find((name) => goal.includes(`helper ${name}`));

export const calls = { n: 0 };
let modelUsed = () => calls.n; // live runs count on the budgeted model; the scripted counter sees only the scripted one

/** Live models fence the code they were told to send bare, and sometimes add prose; keep the first fenced block's code. */
export function codeOf(reply: unknown): string {
  const text = String(reply).trim();
  return (/```[\w-]*[^\S\n]*\n([\s\S]*?)```/.exec(text)?.[1] ?? text).trim();
}

/** A scripted engineer: it writes the helper named in its goal. In `hang` mode the last helper gets no answer. */
export function createModel(hang = false) {
  const respond = async (request: { messages: { role: string; content: unknown }[] }) => {
    calls.n += 1;
    const goal = String(request.messages.filter((m) => m.role === "user").at(-1)?.content ?? "");
    const name = helperOf(goal);
    if (hang && name === "readingTime") await new Promise(() => setInterval(() => {}, 1_000));
    return { content: name ? CODE[name] : "unknown helper" };
  };
  return liveOrScripted({ maxCalls: Number(process.env.OMEGA_HUB_MAX_CALLS ?? 30), scripted: { id: "scripted-engineers", turns: Array.from({ length: 40 }, () => respond) } });
}

const write = (dir: string, name: Helper, code: string) => {
  mkdirSync(join(dir, "src"), { recursive: true });
  writeFileSync(join(dir, "src", `${name}.mjs`), `${code}\n`);
  writeFileSync(join(dir, `check-${name}.mjs`), `${CHECKS[name]}\n`);
};
const runCheck = (dir: string, name: Helper) => spawnSync(process.execPath, [`check-${name}.mjs`], { cwd: dir, encoding: "utf8" });

export async function runTeam(dir: string, { hang = false } = {}) {
  const work = join(dir, "repo");
  mkdirSync(work, { recursive: true });
  const model = createModel(hang);
  modelUsed = () => (isLive() ? (model as { calls: number }).calls : calls.n);
  const log: string[] = [];
  const result = await runVerifiedTaskTree(REQUEST, {
    decompose: async (node) => (node.depth === 0 ? { subgoals: HELPERS.map((name) => ({ goal: `Write the helper ${name} (one exported function): ${CONTRACT[name]}` })) } : { subgoals: [] }),
    solve: async (node) => {
      if (!helperOf(node.goal)) return { output: "the feature is built from its helpers", producer: { id: "lead", lineage: ["engineering"] } };
      const run = await runAgent({ name: "Engineer", instructions: "Write the one exported JavaScript function the goal names, as an ES module, and reply with only the code.", model: model.id, tools: [] }, node.goal, { providers: [model], maxTurns: 1 });
      return { output: codeOf(run.content), producer: { id: "engineer", lineage: ["engineering"] } };
    },
    certify: async (node) => {
      const name = helperOf(node.goal);
      if (!name) {
        const done = node.output === "all checks pass";
        return { verdict: done ? ("pass" as const) : ("fail" as const), evidence: done ? "every helper check passed together" : "the feature is done only when its helpers are", certificateKind: "execute" as const };
      }
      write(work, name, String(node.output));
      const check = runCheck(work, name);
      log.push(`CI ${name}: ${check.status === 0 ? "pass" : "fail"}`);
      return { verdict: check.status === 0 ? ("pass" as const) : ("fail" as const), evidence: `node check-${name}.mjs exited ${check.status}`, certificateKind: "execute" as const };
    },
    compose: (_parent, children) => {
      for (const child of children) {
        const name = helperOf(child.goal);
        if (name) write(work, name, String(child.output));
      }
      const failing = HELPERS.filter((name) => runCheck(work, name).status !== 0);
      log.push(`CI all helpers together: ${failing.length === 0 ? "pass" : `fail (${failing.join(", ")})`}`);
      return failing.length === 0 ? "all checks pass" : `failing: ${failing.join(", ")}`;
    },
    memory: new FileVerifiedTreeMemory(join(dir, "verified.json")),
    maxDepth: 1,
    maxAttemptsPerNode: 2,
    concurrency: 1
  });
  return { result, log, work };
}

/** How many results the verified memory holds, read the way a second process would read it. */
export function verifiedOnDisk(dir: string): number {
  try {
    return JSON.parse(readFileSync(join(dir, "verified.json"), "utf8")).verified.length;
  } catch {
    return 0;
  }
}

const loaderArgs = () => process.execArgv.flatMap((arg, i, all) => (/^--import=/.test(arg) ? [arg] : arg === "--import" ? [arg, all[i + 1]!] : []));

export async function crashAndResume(dir = mkdtempSync(join(tmpdir(), "engineering-team-"))) {
  const env = { ...process.env };
  delete env.OMEGA_LIVE_MODEL;
  const child = spawn(process.execPath, [...loaderArgs(), import.meta.filename, "--run-1", dir], { stdio: "ignore", env });
  const exited = new Promise<{ signal: string | null }>((resolve) => child.once("exit", (_code, signal) => resolve({ signal })));
  for (const started = Date.now(); verifiedOnDisk(dir) < 2; ) {
    if (child.exitCode !== null || Date.now() - started > 60_000) throw new Error("run 1 ended before two helpers were verified");
    await sleep(25);
  }
  child.kill("SIGKILL");
  const killed = await exited;
  const verifiedBefore = verifiedOnDisk(dir);
  calls.n = 0;
  const second = await runTeam(dir);
  return { dir, killed, verifiedBefore, second, modelCallsInRun2: modelUsed() };
}

if (process.argv[2] === "--run-1") {
  await runTeam(process.argv[3]!, { hang: true });
} else if (process.argv[1] === import.meta.filename) {
  const { killed, verifiedBefore, second, modelCallsInRun2 } = await crashAndResume();
  console.log(`run 1: killed with ${killed.signal} while the third engineer waited for its model`);
  console.log(`verified on disk after the kill: ${verifiedBefore} of 3 helpers`);
  for (const line of second.log) console.log(`run 2: ${line}`);
  if (isLive() && !second.result.verified) process.exitCode = 1;
  console.log(`run 2: feature verified: ${second.result.verified}; model calls: ${modelCallsInRun2} (only the unfinished helper)`);
}
