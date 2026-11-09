/**
 * The sandbox, against the real runner image on a runner daemon (Colima, or a deploy shape's runner
 * host through DOCKER_HOST; SPEC §14.1 acceptance 3). Without a runner image the Docker tests skip,
 * saying why; when OMEGA_HUB_RUNNER_IMAGE, OMEGA_HUB_REQUIRE_SANDBOX or CI is set, a missing image or
 * one without this judge FAILS the suite instead, so the network-off and judge proofs cannot look
 * green without having run. OMEGA_HUB_EXPECT_RUNTIME=runsc makes the suite fail unless the daemon's
 * default runtime is gVisor, so the judge tests below are known to have run under runsc (READINESS
 * 3.19). Build the image with runner/build.sh.
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";
import { AS_ENTRY, ENTRY_UID, JUDGE_CAPS, JUDGE_LABEL, JUDGE_NODE, JUDGE_SCRIPT, JUDGE_SHELL } from "../src/judge.mjs";
import { judgePreflight } from "../src/preflight.mjs";
import { runnerIdentity } from "../src/main.mjs";
import { packCanonical } from "../src/pack.mjs";
import { checkSubmission } from "../src/pipeline.mjs";
import { readUpload } from "../src/archive.mjs";
import { dockerArgs, dockerEnv, runContainer, sandboxStep, withoutTest } from "../src/sandbox.mjs";
import { CHECKER, fixture, job, registry, upload } from "./helpers.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const built = path.join(here, "..", "runner", ".built");
const IMAGE = process.env.OMEGA_HUB_RUNNER_IMAGE ?? (existsSync(built) ? readFileSync(built, "utf8").split(" ")[0] : null);
const env = { ...process.env, DOCKER_CONTEXT: process.env.DOCKER_CONTEXT ?? "colima" };
const docker = (...args) => spawnSync("docker", args, { env: dockerEnv(env), encoding: "utf8" });
const judgeLabel = IMAGE ? docker("image", "inspect", IMAGE, "--format", '{{index .Config.Labels "org.futureagi.omega-hub.judge"}}') : null;
const HAVE = Boolean(IMAGE && judgeLabel?.status === 0 && judgeLabel.stdout.trim() === JUDGE_LABEL);
const REQUIRED = Boolean(process.env.OMEGA_HUB_RUNNER_IMAGE || process.env.OMEGA_HUB_REQUIRE_SANDBOX || process.env.CI);
const why = !IMAGE ? "runner/.built missing" : judgeLabel?.status !== 0 ? `docker cannot see ${IMAGE}: ${String(judgeLabel?.stderr ?? "").trim().slice(0, 200)}` : `${IMAGE} has judge '${judgeLabel.stdout.trim()}', not ${JUDGE_LABEL}`;
const skip = HAVE ? false : `no usable runner image (${why}); build one with runner/build.sh`;
const leftovers = () => docker("ps", "-a", "--filter", "name=ohc-", "--format", "{{.Names}}").stdout.trim();
const sandbox = (args) => sandboxStep({ ...args, image: IMAGE, env });
const stepOf = (report, id) => report.steps.find((item) => item.id === id);
const EXPECT_RUNTIME = process.env.OMEGA_HUB_EXPECT_RUNTIME ?? "";
const daemonRuntime = HAVE ? docker("info", "--format", "{{.DefaultRuntime}}").stdout.trim() : "";
const RUNSC = daemonRuntime === "runsc";

after(() => {
  if (HAVE) assert.equal(leftovers(), "", "an ohc-* container was left behind");
});

test("the runner image is reachable and has the judge whenever the sandbox is required", { skip: REQUIRED ? false : "the sandbox is not required here" }, () => {
  assert.ok(HAVE, `the sandbox tests cannot run: ${why}`);
});

test("the runner daemon's default runtime is the one this run expects, and under runsc a container sees gVisor", { skip: EXPECT_RUNTIME ? false : "OMEGA_HUB_EXPECT_RUNTIME is not set" }, () => {
  assert.ok(HAVE, `the sandbox tests cannot run: ${why}`);
  assert.equal(daemonRuntime, EXPECT_RUNTIME);
  if (EXPECT_RUNTIME === "runsc") {
    const seen = docker("run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--user", `${ENTRY_UID}:${ENTRY_UID}`, "--entrypoint", "uname", IMAGE, "-r");
    assert.equal(seen.status, 0, seen.stderr);
    assert.match(seen.stdout.trim(), /gvisor/, `a container on this daemon sees kernel ${seen.stdout.trim()}`);
  }
});

test("docker run carries every isolation flag of SPEC §4.4, the judge's two capabilities and nothing more, and the judge shell is the command", () => {
  const argv = dockerArgs({ name: "ohc-12345678", image: "img", id: "good-agent" });
  const args = argv.join(" ");
  for (const flag of ["--rm", "--network none", "--read-only", "--tmpfs /tmp:rw,size=256m", "--tmpfs /work:rw,size=64m,uid=10001",
    "--user 0:0", "--cap-drop ALL", "--cap-add SETUID --cap-add SETGID", "--security-opt no-new-privileges", "--pids-limit 256", "--memory 1g", "--cpus 1",
    "--env OMEGA_HUB_KEYLESS=1"]) assert.ok(args.includes(flag), flag);
  assert.deepEqual(argv.flatMap((arg, index) => (arg === "--cap-add" ? [argv[index + 1]] : [])), ["SETUID", "SETGID"]);
  const flags = argv.slice(0, argv.indexOf("img")); // the docker flags, without the judge's script
  assert.ok(!flags.includes("--privileged") && !flags.some((flag) => /^(seccomp|apparmor|label)[=:]/.test(flag)), "no isolation switched off");
  assert.ok(!flags.includes("-v") && !flags.includes("--volume") && !flags.includes("--mount"), "no bind mounts");
  assert.deepEqual(argv.slice(-4), ["img", JUDGE_SHELL, "-c", JUDGE_SCRIPT]);
  assert.deepEqual(Object.keys(dockerEnv({ PATH: "/bin", HOME: "/h", SECRET: "x", OPENAI_API_KEY: "y" })).sort(), ["DOCKER_CONTEXT", "HOME", "PATH"]);
});

test("a remote runner daemon's address and mutual-TLS settings reach the docker CLI, and nothing else does", () => {
  const env = dockerEnv({
    PATH: "/bin", DOCKER_HOST: "tcp://hub-runner:2376", DOCKER_TLS_VERIFY: "1", DOCKER_CERT_PATH: "/certs/client",
    DOCKER_CONTEXT: "default", OMEGA_HUB_CHECKER_TOKEN: "t", AWS_SECRET_ACCESS_KEY: "s",
  });
  assert.deepEqual(env, { PATH: "/bin", DOCKER_CONTEXT: "default", DOCKER_HOST: "tcp://hub-runner:2376", DOCKER_TLS_VERIFY: "1", DOCKER_CERT_PATH: "/certs/client" });
});

test("the demo run's bundle is the canonical bundle without the test file, source folder kept", () => {
  const files = fixture("good");
  const { bundle } = packCanonical(files, new Map([["lib/util.mjs", Buffer.from("export const x = 1;\n")]]));
  const names = readUpload(withoutTest(bundle)).members.map((member) => member.name);
  assert.ok(!names.includes("entry/main.test.mjs"));
  assert.deepEqual(names.filter((name) => name !== "source/lib/util.mjs"), [...files.keys()].filter((rel) => rel !== "main.test.mjs").sort().map((rel) => `entry/${rel}`));
  assert.ok(names.includes("source/lib/util.mjs"));
});

test("the checker refuses to start on a runner image without this judge, including the execute-only judge 1", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "ohc-fake-docker-"));
  try {
    const fake = (name, labels) => {
      const file = path.join(dir, name);
      writeFileSync(file, `#!/bin/sh\necho "sha256:0123 ${labels}"\n`);
      chmodSync(file, 0o755);
      return file;
    };
    assert.throws(() => runnerIdentity("omega-hub-runner:old", { PATH: process.env.PATH }, fake("old", "881b05ba5 ")), /has no judge/);
    assert.throws(() => runnerIdentity("omega-hub-runner:x1", { PATH: process.env.PATH }, fake("x1", "881b05ba5 1")), /has judge 1, not 2 .*rebuild it with runner\/build\.sh/);
    assert.deepEqual(runnerIdentity("omega-hub-runner:new", { PATH: process.env.PATH }, fake("new", "881b05ba5 2")), { runnerImage: "sha256:0123", omegaCommit: "881b05ba5" });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("network off is proven: every probe fails with no route, while loopback works", { skip }, async () => {
  const bundle = packCanonical(fixture("curl-probe")).bundle;
  // As entry code runs: uid 10001 with no capabilities (the container starts as the judge, and root
  // without CAP_DAC_OVERRIDE cannot write into the tmpfs /work that uid 10001 owns under runc).
  const probes = 'mkdir -p /work/hub && tar -xzf - -C /work && mv /work/entry /work/hub/curl-probe && cd /work/hub/curl-probe '
    + '&& node -e "import(\\"./probes.mjs\\").then(m => m.runProbes()).then(r => console.log(JSON.stringify(r)))" '
    + '&& node -e "const h=require(\\"node:http\\");const s=h.createServer((q,r)=>r.end(\\"ok\\")).listen(0,\\"127.0.0.1\\",async()=>{const t=await (await fetch(\\"http://127.0.0.1:\\"+s.address().port)).text();console.log(\\"loopback:\\"+t);s.close()})"';
  assert.ok(!probes.includes("'"), "the probe script goes inside single quotes");
  const script = `${AS_ENTRY} /bin/sh -c '${probes}'`;
  const run = await runContainer({ name: "ohc-netproof", image: IMAGE, id: "curl-probe", bundle, script, env, timeoutMs: 60_000 });
  assert.equal(run.code, 0, run.output);
  const results = JSON.parse(run.output.split("\n").find((line) => line.startsWith("[")));
  assert.equal(results.length, 6);
  for (const result of results) assert.equal(result.reached, false, `${result.name} reached the network`);
  const byName = Object.fromEntries(results.map((result) => [result.name, result.code]));
  for (const name of ["fetch", "net.connect", "net.connect in a Worker"]) assert.equal(byName[name], "ENETUNREACH", name);
  assert.match(run.output, /loopback:ok/);
});

test("no host variable reaches the container", { skip }, async () => {
  const run = await runContainer({ name: "ohc-envdump", image: IMAGE, id: "x", bundle: Buffer.alloc(0), script: "env",
    env: { ...env, OHC_SENTINEL_SECRET: "leak-me-0123456789", OPENAI_API_KEY: "sk-should-not-pass" } });
  assert.equal(run.code, 0, run.output);
  assert.ok(!run.output.includes("OHC_SENTINEL_SECRET") && !run.output.includes("sk-should-not-pass"));
  assert.ok(!run.output.includes(process.env.HOME ?? "/Users/"), "the host HOME leaked");
  assert.match(run.output, /OMEGA_HUB_KEYLESS=1/);
});

test("the judge loads and holds on this daemon: root with CAP_SETUID and CAP_SETGID only; entry code uid 10001 with no capability, refused the judge", { skip, timeout: 120_000 }, async (t) => {
  const result = await judgePreflight({ image: IMAGE, env });
  assert.equal(result.ok, true, `on the ${daemonRuntime} daemon: ${result.reason}`);
  assert.match(result.judgeLine, new RegExp(`^judge uid=0 gid=0 caps=${JUDGE_CAPS} `));
  assert.deepEqual([result.report.entry.uid, result.report.entry.gid, result.report.entry.capEff], [ENTRY_UID, ENTRY_UID, "0000000000000000"]);
  t.diagnostic(`runtime ${daemonRuntime}: ${result.judgeLine}; entry ${JSON.stringify(result.report.entry)}; sync child uid ${result.report.syncUid}`);
});

/**
 * A copy of the runner image where `file` (followed through symlinks) belongs to uid 1234 with mode
 * 0711: the judge's runtime uid (root without CAP_DAC_OVERRIDE) and uid 10001 may execute it but not
 * read it. Built with a COPY-only Dockerfile on the runner daemon: a file changed inside a running
 * runsc container would not reach `docker commit`, because gVisor keeps the root overlay's upper layer
 * in memory.
 */
function executeOnlyCopy(file) {
  const id = randomBytes(3).toString("hex");
  const base = `omega-hub-runner-variant-base:${id}`;
  const tag = `omega-hub-runner-variant:${id}`;
  const real = docker("run", "--rm", "--network", "none", "--entrypoint", "readlink", IMAGE, "-f", file).stdout.trim();
  assert.match(real, /^\/[\w./-]+$/, `readlink -f ${file}`);
  assert.equal(docker("tag", IMAGE, base).status, 0);
  try {
    const built = spawnSync("docker", ["build", "-q", "-t", tag, "-"], {
      env: dockerEnv(env), encoding: "utf8", input: `FROM ${base}\nCOPY --from=${base} --chown=1234:1234 --chmod=0711 ${real} ${real}\n`,
    });
    assert.equal(built.status, 0, built.stderr);
  } finally {
    docker("rmi", base);
  }
  const stat = docker("run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "stat", tag, "-c", "%n %u:%g %a", real).stdout.trim();
  assert.equal(stat, `${real} 1234:1234 711`, "the copy does not have the execute-only file");
  return { tag, stat };
}

test("under gVisor, a judge that its runtime uid may execute but not read is refused: the judge shell, then the judge node", { skip: skip || (RUNSC ? false : `the daemon's default runtime is ${daemonRuntime || "unknown"}, not runsc`), timeout: 300_000 }, async (t) => {
  for (const file of [JUDGE_SHELL, JUDGE_NODE]) {
    const { tag, stat } = executeOnlyCopy(file);
    try {
      const result = await judgePreflight({ image: tag, env });
      assert.equal(result.ok, false, `${stat}: the preflight passed: ${result.judgeLine}`);
      assert.ok([65, 69].includes(result.code), `${stat}: code ${result.code}`);
      t.diagnostic(`${stat}: refused with ${result.code}: ${result.reason.slice(0, 300)}`);
    } finally {
      docker("rmi", "-f", tag);
    }
  }
});

test("good passes its keyless test and demo in the sandbox", { skip, timeout: 300_000 }, async () => {
  const { report, log } = await checkSubmission({ job: job({ hints: { name: "good-agent" } }), raw: upload(fixture("good")), registry: registry(), sandbox, checker: CHECKER });
  const step = report.steps.find((item) => item.id === "sandbox-run");
  assert.equal(step.status, "pass", JSON.stringify(step.findings) + log.slice(-800));
  assert.equal(step.network, "none");
  assert.equal(report.outcome, "in_review");
});

test("curl-probe runs with every probe stopped, and is flagged for review", { skip, timeout: 300_000 }, async () => {
  const { report } = await checkSubmission({ job: job(), raw: upload(fixture("curl-probe")), registry: registry(), sandbox, checker: CHECKER });
  assert.equal(report.steps.find((item) => item.id === "sandbox-run").status, "pass");
  const flags = report.steps.flatMap((item) => item.findings).filter((item) => item.severity === "flag").map((item) => item.rule);
  for (const rule of ["child-process", "worker-threads", "raw-network", "disclosure-gap"]) assert.ok(flags.includes(rule), rule);
  assert.equal(report.outcome, "in_review");
});

test("a run past the time limit is killed and fails", { skip, timeout: 120_000 }, async () => {
  const files = fixture("good");
  files.set("main.test.mjs", Buffer.from('import { test } from "node:test";\ntest("hangs", () => new Promise(() => setInterval(() => {}, 1000)));\n'));
  const bundle = packCanonical(files).bundle;
  const { step } = await sandboxStep({ jobId: "deadbeef-0000", image: IMAGE, id: "good-agent", source: "", bundle, manifest: {}, timeoutMs: 8_000, env });
  assert.equal(step.status, "fail");
  assert.equal(step.findings[0].rule, "timeout");
  await new Promise((resolve) => setTimeout(resolve, 2000));
  assert.equal(leftovers(), "");
});

test("a submission that forges a passing verdict and tries to kill the CLI ends in checks_failed with its real failures", { skip, timeout: 300_000 }, async () => {
  const { report, log } = await checkSubmission({ job: job({ hints: { name: "good-agent" } }), raw: upload(fixture("forge-verdict")), registry: registry(), sandbox, checker: CHECKER });
  const step = stepOf(report, "sandbox-run");
  assert.equal(step.status, "fail", log.slice(-800));
  // The CLI runs as root and entry code as uid 10001, so the SIGKILL is refused: the CLI finishes, its
  // frame is accepted, and the test run's real R14 and the demo run's real R15 are the findings.
  assert.deepEqual(step.findings.map((item) => item.rule).sort(), ["R14", "R15"], JSON.stringify(step.findings) + log.slice(-600));
  assert.equal(report.outcome, "checks_failed");
  const flags = stepOf(report, "static-scan").findings.filter((item) => item.severity === "flag").map((item) => item.rule);
  for (const rule of ["proc-access", "signals-processes"]) assert.ok(flags.includes(rule), rule);
});

test("a submission that only forges the verdict gets its real R14 and R15 failures", { skip, timeout: 300_000 }, async () => {
  const files = fixture("forge-verdict");
  files.set("main.test.mjs", Buffer.from(files.get("main.test.mjs").toString("utf8").replace("const KILL = true;", "const KILL = false;")));
  const { report, log } = await checkSubmission({ job: job({ hints: { name: "good-agent" } }), raw: upload(files), registry: registry(), sandbox, checker: CHECKER });
  const step = stepOf(report, "sandbox-run");
  assert.deepEqual(step.findings.map((item) => item.rule).sort(), ["R14", "R15"], JSON.stringify(step.findings) + log.slice(-600));
  assert.equal(report.outcome, "checks_failed");
});

test("the judge refuses every probe from inside the entry: no fd writes, environ, mem or signals at a judge process, no inspector", { skip, timeout: 300_000 }, async (t) => {
  const bundle = packCanonical(fixture("judge-probe")).bundle;
  const { step, log } = await sandboxStep({ jobId: "a11ce000-0000", image: IMAGE, id: "good-agent", source: "", bundle, manifest: {}, env });
  const r14 = step.findings.find((item) => item.rule === "R14");
  assert.ok(r14, JSON.stringify(step.findings) + log.slice(-600));
  assert.ok(!step.findings.some((item) => item.rule === "judge"), "the CLI survived the probes and its frame was accepted");
  const probes = JSON.parse(/PROBES (\{.*\})/.exec(r14.message)[1]);
  t.diagnostic(`runtime ${daemonRuntime}: ${JSON.stringify(probes)}`);
  // chain: "<pid>:<uid>:<argv0>" from the test process's parent up to PID 1. The node test runner is
  // entry code (uid 10001); the CLI and the judge shell are the judge (uid 0). Nothing else.
  const links = probes.chain.map((link) => link.split(":"));
  assert.ok(links.length >= 3 && links.at(-1)[0] === "1" && links.at(-1)[1] === "0", probes.chain.join(" "));
  for (const [pid, uid] of links) assert.ok(uid === "0" || uid === String(ENTRY_UID), `ancestor ${pid} runs as ${uid}`);
  const judge = new Set(links.filter(([, uid]) => uid === "0").map(([pid]) => pid));
  assert.equal(probes.cli.length, 1, JSON.stringify(probes.cli));
  assert.ok(judge.has(probes.cli[0]) && judge.has("1"), `the CLI ${probes.cli[0]} and PID 1 run as root: ${probes.chain.join(" ")}`);
  let judged = 0;
  for (const [probe, outcome] of Object.entries(probes.results)) {
    const pid = probe.split(" ")[0];
    if (probe.startsWith("inspector")) assert.equal(outcome, "ECONNREFUSED", probe);
    else if (judge.has(pid)) {
      judged += 1;
      assert.ok(["EACCES", "EPERM"].includes(outcome), `${probe}: ${outcome}`);
    }
  }
  assert.ok(judged >= 2 * 3 + 2, `only ${judged} probes reached a judge process: ${JSON.stringify(probes)}`);
});

test("a keyless test that rewrites demo.cast cannot make a stale recording pass R15", { skip, timeout: 300_000 }, async () => {
  // main.mjs now prints something the uploaded demo.cast never showed, and the test rewrites demo.cast at
  // run time to match. The demo run has no test file, so R15 compares the cast that was uploaded.
  const files = fixture("good");
  const changed = "answer: something the recording never showed";
  files.set("main.mjs", Buffer.from(files.get("main.mjs").toString("utf8").replace('console.log("answer:", result.content);', `console.log(${JSON.stringify(changed)});`)));
  const test = files.get("main.test.mjs").toString("utf8").replace("assert.match(run.stdout, /^answer: In Lisbon today: sunny, 24 degrees\\.$/m);", "");
  files.set("main.test.mjs", Buffer.from(`${test}
import { readFileSync as readText, writeFileSync as writeText } from "node:fs";
test("rewrite the recording", () => {
  const cast = new URL("./demo.cast", import.meta.url);
  writeText(cast, readText(cast, "utf8").replace("answer: In Lisbon today: sunny, 24 degrees.", ${JSON.stringify(changed)}));
});
`));
  assert.ok(files.get("main.test.mjs").toString("utf8").includes("rewrite the recording") && !files.get("main.test.mjs").toString("utf8").includes("/^answer: In Lisbon"));
  const { report, log } = await checkSubmission({ job: job({ hints: { name: "good-agent" } }), raw: upload(files), registry: registry(), sandbox, checker: CHECKER });
  const step = stepOf(report, "sandbox-run");
  assert.deepEqual(step.findings.map((item) => item.rule), ["R15"], JSON.stringify(step.findings) + log.slice(-600));
  assert.match(step.findings[0].message, /stale/);
  assert.equal(report.outcome, "checks_failed");
});
