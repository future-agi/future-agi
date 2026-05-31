/* eslint-disable no-console */
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";
import { validateProofPack } from "./validate-onboarding-proof-pack.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

const SMOKES = [
  {
    id: "setup-org-completion-controlled",
    mode: "controlled",
    file: "setup-org-completion-smoke.mjs",
    description:
      "Stubbed auth proof for setup-org quick start into onboarding Home.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "setup-org-sample-preview-controlled",
    mode: "controlled",
    file: "setup-org-completion-smoke.mjs",
    description:
      "Stubbed auth proof for setup-org sample preview quick start into the sample Aha panel.",
    env: {
      ONBOARDING_SMOKE_SETUP_SAMPLE_PREVIEW: "1",
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "setup-org-prompt-controlled",
    mode: "controlled",
    file: "setup-org-completion-smoke.mjs",
    description:
      "Stubbed auth proof for setup-org prompt quick start into the prompt path focus.",
    env: {
      ONBOARDING_SMOKE_SETUP_QUICK_START: "prompt",
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "setup-org-agent-controlled",
    mode: "controlled",
    file: "setup-org-completion-smoke.mjs",
    description:
      "Stubbed auth proof for setup-org agent quick start into the agent path focus.",
    env: {
      ONBOARDING_SMOKE_SETUP_QUICK_START: "agent",
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "setup-org-gateway-controlled",
    mode: "controlled",
    file: "setup-org-completion-smoke.mjs",
    description:
      "Stubbed auth proof for setup-org gateway quick start into the gateway path focus.",
    env: {
      ONBOARDING_SMOKE_SETUP_QUICK_START: "gateway",
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "setup-org-evals-controlled",
    mode: "controlled",
    file: "setup-org-completion-smoke.mjs",
    description:
      "Stubbed auth proof for setup-org eval quick start into the eval path focus.",
    env: {
      ONBOARDING_SMOKE_SETUP_QUICK_START: "evals",
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "setup-org-voice-controlled",
    mode: "controlled",
    file: "setup-org-completion-smoke.mjs",
    description:
      "Stubbed auth proof for setup-org voice quick start into the voice path focus.",
    env: {
      ONBOARDING_SMOKE_SETUP_QUICK_START: "voice",
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "onboarding-home-observe-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Stubbed auth proof for Home Observe CTA into Observe setup focus.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "onboarding-home-observe-existing-project-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Stubbed auth proof for Home Observe CTA into the existing-project first trace step.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_EXISTING_PROJECT: "1",
    },
  },
  {
    id: "onboarding-home-sample-open-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Stubbed auth proof that Home sample Aha CTA opens the seeded trace route.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_OPEN_SAMPLE: "1",
    },
  },
  {
    id: "onboarding-observe-project-first-trace-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Stubbed auth proof for Observe project first trace arrival into trace review.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_EXISTING_PROJECT: "1",
      ONBOARDING_SMOKE_EXISTING_TRACE: "1",
    },
  },
  {
    id: "onboarding-first-trace-review-controlled",
    mode: "controlled",
    file: "onboarding-first-trace-review-smoke.mjs",
    description:
      "Stubbed auth proof for first trace ready state into trace review.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
    },
  },
  {
    id: "onboarding-post-aha-fallback-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Stubbed auth proof that post-Aha Home stays actionable without Daily Quality.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_POST_AHA_HOME: "1",
    },
  },
  {
    id: "onboarding-post-aha-fallback-mobile-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Mobile stubbed proof that post-Aha Home stays actionable without Daily Quality.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_POST_AHA_HOME: "1",
      ONBOARDING_SMOKE_VIEWPORT: "mobile",
    },
  },
  {
    id: "onboarding-get-started-fallback-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Stubbed auth proof that disabled onboarding Home keeps Get Started available.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_FEATURE_DISABLED_HOME: "1",
    },
  },
  {
    id: "onboarding-home-evals-path-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Stubbed auth proof that Home shows the eval first-run path focus.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_PATH_FOCUS: "evals",
    },
  },
  {
    id: "onboarding-home-voice-path-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Stubbed auth proof that Home shows the voice first-run path focus.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_PATH_FOCUS: "voice",
    },
  },
  {
    id: "onboarding-home-evals-path-mobile-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Mobile stubbed proof that Home shows the eval first-run path focus.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_PATH_FOCUS: "evals",
      ONBOARDING_SMOKE_VIEWPORT: "mobile",
    },
  },
  {
    id: "onboarding-home-voice-path-mobile-controlled",
    mode: "controlled",
    file: "onboarding-home-observe-smoke.mjs",
    description:
      "Mobile stubbed proof that Home shows the voice first-run path focus.",
    env: {
      ONBOARDING_SMOKE_STUB_AUTH: "1",
      ONBOARDING_SMOKE_PATH_FOCUS: "voice",
      ONBOARDING_SMOKE_VIEWPORT: "mobile",
    },
  },
  {
    id: "prompt-first-run-controlled",
    mode: "controlled",
    file: "prompt-first-run-controlled-smoke.mjs",
    description:
      "Stubbed proof for prompt create, first run, second version, compare, and failure capture.",
  },
  {
    id: "onboarding-first-signup-aha-coverage-controlled",
    mode: "controlled",
    suite: true,
    sequence: [
      "setup-org-completion-controlled",
      "setup-org-sample-preview-controlled",
      "setup-org-prompt-controlled",
      "setup-org-agent-controlled",
      "setup-org-gateway-controlled",
      "setup-org-evals-controlled",
      "setup-org-voice-controlled",
      "onboarding-home-observe-controlled",
      "onboarding-home-sample-open-controlled",
      "onboarding-observe-project-first-trace-controlled",
      "onboarding-first-trace-review-controlled",
      "onboarding-post-aha-fallback-controlled",
    ],
    description:
      "Composite controlled proof for the first-signup Aha screen sequence.",
  },
  {
    id: "onboarding-first-signup-aha-coverage-mobile-controlled",
    mode: "controlled",
    suite: true,
    sequence: [
      "setup-org-completion-controlled",
      "setup-org-sample-preview-controlled",
      "setup-org-prompt-controlled",
      "setup-org-agent-controlled",
      "setup-org-gateway-controlled",
      "setup-org-evals-controlled",
      "setup-org-voice-controlled",
      "onboarding-home-observe-controlled",
      "onboarding-home-sample-open-controlled",
      "onboarding-observe-project-first-trace-controlled",
      "onboarding-first-trace-review-controlled",
      "onboarding-post-aha-fallback-controlled",
    ],
    description:
      "Mobile composite controlled proof for the first-signup Aha screen sequence.",
    env: {
      ONBOARDING_SMOKE_VIEWPORT: "mobile",
    },
  },
  {
    id: "signup-quick-start-real",
    mode: "real-signup",
    file: "signup-quick-start-smoke.mjs",
    description:
      "Disposable-account proof from signup through first Observe/Eval quality loop.",
    env: {
      ONBOARDING_REAL_SIGNUP: "1",
    },
  },
  {
    id: "signup-quick-start-mobile-real",
    mode: "real-signup",
    file: "signup-quick-start-smoke.mjs",
    description:
      "Mobile disposable-account proof from signup through first Observe/Eval quality loop.",
    env: {
      ONBOARDING_REAL_SIGNUP: "1",
      ONBOARDING_SMOKE_VIEWPORT: "mobile",
    },
  },
  {
    id: "signup-sample-open-real",
    mode: "real-signup",
    file: "signup-quick-start-smoke.mjs",
    description:
      "Disposable-account proof that sample trace opens and stays non-activating.",
    env: {
      ONBOARDING_REAL_SIGNUP: "1",
      ONBOARDING_REAL_SIGNUP_SAMPLE_ONLY: "1",
    },
  },
  {
    id: "signup-sample-open-mobile-real",
    mode: "real-signup",
    file: "signup-quick-start-smoke.mjs",
    description:
      "Mobile disposable-account proof that sample trace opens and stays non-activating.",
    env: {
      ONBOARDING_REAL_SIGNUP: "1",
      ONBOARDING_REAL_SIGNUP_SAMPLE_ONLY: "1",
      ONBOARDING_SMOKE_VIEWPORT: "mobile",
    },
  },
  {
    id: "signup-real-proof-pack",
    mode: "real-signup",
    suite: true,
    continueOnFailure: true,
    sequence: [
      "signup-sample-open-real",
      "signup-sample-open-mobile-real",
      "signup-quick-start-real",
      "signup-quick-start-mobile-real",
    ],
    description:
      "Disposable-account desktop and mobile proof pack for sample Aha and first quality loop.",
  },
];

const args = parseArgs(process.argv.slice(2));

if (args.list) {
  for (const smoke of SMOKES) {
    const target = smoke.sequence
      ? `suite:${smoke.sequence.join(",")}`
      : `node scripts/api-journeys/browser/${smoke.file}`;
    console.log([smoke.id, smoke.mode, target, smoke.description].join("\t"));
  }
  process.exit(0);
}

const selected = SMOKES.filter((smoke) => {
  if (args.mode && smoke.mode !== args.mode) return false;
  if (args.only.size && !args.only.has(smoke.id)) return false;
  if (smoke.suite && !args.only.has(smoke.id)) return false;
  return true;
});

if (selected.length === 0) {
  throw new Error("No onboarding smoke scripts matched the requested filters.");
}

if (args.reportOutput) {
  if (args.reportOutputDir) {
    throw new Error(
      "--report-output and --report-output-dir are mutually exclusive.",
    );
  }
  if (selected.length !== 1 || selected[0].suite) {
    throw new Error(
      "--report-output can only be used with one non-suite smoke.",
    );
  }
  if (selected[0].mode !== "real-signup") {
    throw new Error(
      "--report-output can only be used with real-signup smokes.",
    );
  }
}
if (args.reportOutputDir) {
  const nonRealSignup = selected.find((smoke) => smoke.mode !== "real-signup");
  if (nonRealSignup) {
    throw new Error(
      "--report-output-dir can only be used with real-signup smokes.",
    );
  }
}

const runnerEnv = args.reportOutput
  ? { ONBOARDING_SMOKE_REPORT_OUTPUT: args.reportOutput }
  : {};

for (const smoke of selected) {
  await runSmoke(smoke, [], runnerEnv);
}

function parseArgs(argv) {
  const parsed = {
    list: false,
    mode: "",
    only: new Set(),
    reportOutput: "",
    reportOutputDir: "",
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--list") {
      parsed.list = true;
    } else if (arg === "--mode") {
      parsed.mode = argv[++index] || "";
    } else if (arg === "--only") {
      for (const id of String(argv[++index] || "").split(",")) {
        if (id.trim()) parsed.only.add(id.trim());
      }
    } else if (arg === "--report-output") {
      const output = argv[++index] || "";
      if (!output.trim()) {
        throw new Error("--report-output requires a non-empty path.");
      }
      parsed.reportOutput = output;
    } else if (arg === "--report-output-dir") {
      const outputDir = argv[++index] || "";
      if (!outputDir.trim()) {
        throw new Error("--report-output-dir requires a non-empty path.");
      }
      parsed.reportOutputDir = outputDir;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (parsed.mode && !["controlled", "real-signup"].includes(parsed.mode)) {
    throw new Error(`Unsupported onboarding smoke mode: ${parsed.mode}`);
  }

  return parsed;
}

async function runSmoke(smoke, stack, inheritedEnv) {
  const env = {
    ...inheritedEnv,
    ...smoke.env,
  };

  if (smoke.sequence) {
    if (stack.includes(smoke.id)) {
      throw new Error(`Onboarding smoke suite cycle: ${stack.join(" -> ")}`);
    }
    console.log(`RUN ${smoke.id} ${smoke.description}`);
    const failures = [];
    const childResults = [];
    for (const childId of smoke.sequence) {
      const child = SMOKES.find((candidate) => candidate.id === childId);
      if (!child) {
        throw new Error(`Unknown onboarding smoke in suite: ${childId}`);
      }
      try {
        await runSmoke(child, [...stack, smoke.id], env);
        childResults.push({
          id: child.id,
          reportPath: reportOutputPath(child),
          status: "passed",
        });
      } catch (error) {
        if (!smoke.continueOnFailure) throw error;
        const message = error?.message || String(error);
        childResults.push({
          id: child.id,
          error_message: message,
          reportPath: reportOutputPath(child),
          status: "failed",
        });
        failures.push({
          id: child.id,
          message,
        });
        console.error(`FAIL ${child.id}: ${message}`);
      }
    }
    const validation = await writeSuiteManifest(smoke, childResults, failures);
    if (failures.length > 0) {
      throw new Error(
        `${smoke.id} failed: ${failures
          .map((failure) => `${failure.id} (${failure.message})`)
          .join("; ")}`,
      );
    }
    if (validation?.status === "failed") {
      throw new Error(
        `${smoke.id} validation failed: ${validation.failed_checks
          .map((check) => check.key)
          .join(", ")}`,
      );
    }
    console.log(`PASS ${smoke.id}`);
    return;
  }

  const scriptPath = resolve(SCRIPT_DIR, smoke.file);
  console.log(`RUN ${smoke.id} ${smoke.description}`);
  const smokeEnv = {
    ...env,
    ...reportOutputDirEnv(smoke),
  };

  const exitCode = await new Promise((resolveExit) => {
    const child = spawn(process.execPath, [scriptPath], {
      env: {
        ...process.env,
        ...smokeEnv,
      },
      stdio: "inherit",
    });

    child.on("close", resolveExit);
    child.on("error", (error) => {
      console.error(error);
      resolveExit(1);
    });
  });

  if (exitCode !== 0) {
    throw new Error(`${smoke.id} failed with exit code ${exitCode}`);
  }

  console.log(`PASS ${smoke.id}`);
}

function reportOutputDirEnv(smoke) {
  if (!args.reportOutputDir) return {};
  return {
    ONBOARDING_SMOKE_REPORT_OUTPUT: reportOutputPath(smoke),
  };
}

function reportOutputPath(smoke) {
  if (!args.reportOutputDir) return null;
  return resolve(args.reportOutputDir, `${smoke.id}.json`);
}

function proofPackValidationOutputPath() {
  return resolve(args.reportOutputDir, "validation.json");
}

async function writeSuiteManifest(smoke, childResults, failures) {
  if (!args.reportOutputDir || !smoke.continueOnFailure) return null;
  const manifestPath = resolve(args.reportOutputDir, "manifest.json");
  const children = await Promise.all(
    childResults.map(async (child) => {
      const report = await readJsonFile(child.reportPath);
      return {
        id: child.id,
        mode: report?.mode || null,
        report_path: child.reportPath,
        report_status: report?.status || null,
        runner_status: child.status,
        viewport: report?.viewport?.name || null,
        error_message:
          child.error_message || report?.diagnostic?.error_message || null,
      };
    }),
  );
  const manifest = {
    schema_version: "onboarding-real-signup-proof-pack-manifest-2026-05-30.v1",
    source: "onboarding_real_signup_proof_pack",
    generated_at: new Date().toISOString(),
    suite_id: smoke.id,
    status: failures.length > 0 ? "failed" : "passed",
    report_output_dir: resolve(args.reportOutputDir),
    children,
  };
  await mkdir(args.reportOutputDir, { recursive: true });
  await writeFile(
    manifestPath,
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );
  console.log(`WROTE ${manifestPath}`);
  if (smoke.id !== "signup-real-proof-pack") return null;

  const validation = await validateProofPack(manifestPath);
  const validationPath = proofPackValidationOutputPath();
  await writeFile(
    validationPath,
    `${JSON.stringify(validation, null, 2)}\n`,
    "utf8",
  );
  console.log(`WROTE ${validationPath}`);
  return validation;
}

async function readJsonFile(path) {
  if (!path) return null;
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch {
    return null;
  }
}
