/* eslint-env node */
// Starts the BUILT `omega dev` (Omega's packages/cli/dist, from the accepted
// studio-embed head) as a real OS process on a copy of Studio's fixture
// project, with its own HOME/XDG/git config and no provider or tracing keys.
// The fixture's `router` entry uses a deterministic function model, so the
// only fake in the Studio e2e is the model.
//
//   STUDIO_OMEGA_REPO  the Omega checkout whose packages/cli/dist to run
//                      (default: <worktree>/.omega/build/omega-studio-embed,
//                      a read-only copy of the accepted head built there)
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const WORKTREE = path.resolve(__dirname, "../../../../../..");
const OMEGA_REPO =
  process.env.STUDIO_OMEGA_REPO ||
  path.join(WORKTREE, ".omega/build/omega-studio-embed");
const CLI = path.join(OMEGA_REPO, "packages/cli/dist/index.js");
const FIXTURE = path.join(
  OMEGA_REPO,
  "packages/studio/fixtures/studio-fixture",
);
const STUDIO_MODULES = path.join(OMEGA_REPO, "packages/studio/node_modules");
const WORK =
  process.env.STUDIO_E2E_WORK || path.join(os.tmpdir(), "omega-studio-tab-e2e");

const DROPPED_ENV =
  /^(OTEL_|OMEGA_TRACE|FI_)|_API_KEY$|_TOKEN$|_SECRET|^GOOGLE_|^VERTEX|^ANTHROPIC|^OPENAI|^GEMINI|^AWS_/;

const running = new Set();

function workspace(name) {
  fs.mkdirSync(WORK, { recursive: true });
  const dir = fs.realpathSync(
    fs.mkdtempSync(path.join(WORK, `omega-dev-${name}-`)),
  );
  const project = path.join(dir, "project");
  fs.cpSync(FIXTURE, project, { recursive: true });
  fs.symlinkSync(STUDIO_MODULES, path.join(project, "node_modules"), "dir");
  const env = {
    ...Object.fromEntries(
      Object.entries(process.env).filter(([key]) => !DROPPED_ENV.test(key)),
    ),
    HOME: path.join(dir, "home"),
    XDG_CONFIG_HOME: path.join(dir, "xdg-config"),
    XDG_DATA_HOME: path.join(dir, "xdg-data"),
    XDG_CACHE_HOME: path.join(dir, "xdg-cache"),
    XDG_STATE_HOME: path.join(dir, "xdg-state"),
    GIT_CONFIG_GLOBAL: path.join(dir, "gitconfig"),
    OMEGA_NO_UPDATE_CHECK: "1",
    OMEGA_FI_TRACING: "off",
  };
  for (const key of [
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
  ]) {
    fs.mkdirSync(env[key], { recursive: true });
  }
  return { dir, project, env };
}

/**
 * Starts omega dev and resolves once it printed its links.
 * @param {object} options
 * @param {string[]} [options.embedOrigins]  --embed-origin values
 * @param {string} options.name              workspace name
 * @returns {Promise<{url, port, token, localLink, futureAgiLink, stdout,
 *   api, stop}>}
 */
function startOmegaDev({ name, embedOrigins = [] }) {
  if (!fs.existsSync(CLI)) {
    throw new Error(
      `omega dev is not built at ${CLI}; build the accepted studio-embed head there first (see e2e/README.md)`,
    );
  }
  const ws = workspace(name);
  const args = [CLI, "dev", "--port", "0", "--reload", "off"];
  for (const origin of embedOrigins) args.push("--embed-origin", origin);
  const child = spawn(process.execPath, args, {
    cwd: ws.project,
    env: ws.env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  running.add(child);
  let out = "";
  let err = "";
  const exited = new Promise((resolve) => {
    child.on("close", (code, signal) => {
      running.delete(child);
      resolve({ code, signal });
    });
  });

  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGTERM");
      const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
      await exited;
      clearTimeout(timer);
    }
    fs.rmSync(ws.dir, { recursive: true, force: true });
  };

  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`omega dev did not start in 90 s:\n${out}${err}`)),
      90_000,
    );
    child.stdout.on("data", (chunk) => {
      out += String(chunk);
      const local = out.match(
        /Open (http:\/\/127\.0\.0\.1:(\d+))\/#t=([A-Za-z0-9_-]{43})\n/,
      );
      if (!local || !out.includes("Runs are kept in")) return;
      clearTimeout(timer);
      const url = local[1];
      const token = local[3];
      const futureAgiLink =
        /^Open in Future AGI: (\S+)$/m.exec(out)?.[1] ?? null;
      const api = async (route) => {
        const response = await fetch(url + route, {
          headers: { authorization: `Bearer ${token}` },
        });
        if (!response.ok) {
          throw new Error(
            `${route}: ${response.status} ${await response.text()}`,
          );
        }
        return response.json();
      };
      resolve({
        url,
        port: Number(local[2]),
        token,
        localLink: `${url}/#t=${token}`,
        futureAgiLink,
        stdout: () => out,
        api,
        stop,
      });
    });
    child.stderr.on("data", (chunk) => {
      err += String(chunk);
    });
    exited.then((exit) => {
      clearTimeout(timer);
      reject(
        new Error(
          `omega dev exited ${exit.code ?? exit.signal} before listening:\n${err}`,
        ),
      );
    });
  });
}

function killAllOmegaDev() {
  for (const child of running) child.kill("SIGKILL");
}

// A loopback port with nothing listening on it (for "not running").
async function deadPort() {
  const net = require("node:net");
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

module.exports = { startOmegaDev, killAllOmegaDev, deadPort, CLI, OMEGA_REPO };
