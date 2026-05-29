/* eslint-disable no-console */
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";

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
    id: "signup-quick-start-real",
    mode: "real-signup",
    file: "signup-quick-start-smoke.mjs",
    description:
      "Disposable-account proof from signup through first Observe/Eval quality loop.",
    env: {
      ONBOARDING_REAL_SIGNUP: "1",
    },
  },
];

const args = parseArgs(process.argv.slice(2));

if (args.list) {
  for (const smoke of SMOKES) {
    console.log(
      [
        smoke.id,
        smoke.mode,
        `node scripts/api-journeys/browser/${smoke.file}`,
        smoke.description,
      ].join("\t"),
    );
  }
  process.exit(0);
}

const selected = SMOKES.filter((smoke) => {
  if (args.mode && smoke.mode !== args.mode) return false;
  if (args.only.size && !args.only.has(smoke.id)) return false;
  return true;
});

if (selected.length === 0) {
  throw new Error("No onboarding smoke scripts matched the requested filters.");
}

for (const smoke of selected) {
  await runSmoke(smoke);
}

function parseArgs(argv) {
  const parsed = {
    list: false,
    mode: "",
    only: new Set(),
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
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (parsed.mode && !["controlled", "real-signup"].includes(parsed.mode)) {
    throw new Error(`Unsupported onboarding smoke mode: ${parsed.mode}`);
  }

  return parsed;
}

async function runSmoke(smoke) {
  const scriptPath = resolve(SCRIPT_DIR, smoke.file);
  console.log(`RUN ${smoke.id} ${smoke.description}`);

  const exitCode = await new Promise((resolveExit) => {
    const child = spawn(process.execPath, [scriptPath], {
      env: {
        ...process.env,
        ...smoke.env,
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
