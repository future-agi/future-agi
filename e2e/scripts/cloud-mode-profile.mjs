// Contract 713: attach metadata only. Never source a shell file, enumerate an
// overlay, or open its signing file. NOT ACCEPTED until main supplies a head/job.
import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import path from "node:path";

export const CLOUD_MODE_ACCEPTED = false;
export const CLOUD_MODE_VARIABLES = [
  "E2E_PROFILE",
  "E2E_CLOUD_MODE_OVERLAY",
  "E2E_STACK",
  "E2E_COMPOSE_PROJECT",
  "E2E_REDIS_URL",
  "E2E_STRIPE_SHIM_URL",
  "E2E_APP_URL",
  "E2E_API_URL",
  "E2E_GATEWAY_URL",
  "E2E_COLLECTOR_URL",
  "E2E_MOCK_LLM_URL",
  "E2E_WEBHOOK_SINK_URL",
  "E2E_MAIL_SINK_URL",
  "E2E_CH_URL",
  "E2E_PG_URL",
];

export function parseCloudModeProfile(source, format) {
  const raw =
    format === "json"
      ? JSON.parse(source)
      : Object.fromEntries(
          source.split(/\r?\n/).flatMap((line) => {
            if (!line.trim() || line.trimStart().startsWith("#")) return [];
            const m = /^(?:export )?([A-Z0-9_]+)=(.*)$/.exec(line);
            if (!m) throw new Error("invalid profile.env assignment");
            let value = m[2];
            if (
              (value.startsWith('"') && value.endsWith('"')) ||
              (value.startsWith("'") && value.endsWith("'"))
            )
              value = value.slice(1, -1);
            return [[m[1], value]];
          }),
        );
  const env = Object.fromEntries(
    CLOUD_MODE_VARIABLES.filter((k) => Object.hasOwn(raw, k)).map((k) => {
      if (typeof raw[k] !== "string" || /[\r\n\0`$]/.test(raw[k]))
        throw new Error(`invalid ${k} in cloud-mode profile`);
      return [k, raw[k]];
    }),
  );
  if (env.E2E_PROFILE !== "cloud-mode" || env.E2E_STACK !== "standalone")
    throw new Error("not a cloud-mode standalone profile");
  if (
    !/^futureagi-e2e-cloudmode-[a-z0-9][a-z0-9-]+$/.test(
      env.E2E_COMPOSE_PROJECT ?? "",
    )
  )
    throw new Error(
      "cloud-mode needs the per-attempt E2E_COMPOSE_PROJECT from profile.env/json",
    );
  for (const name of CLOUD_MODE_VARIABLES.filter((k) => k.endsWith("_URL"))) {
    if (!env[name]) continue;
    const url = new URL(env[name]);
    if (
      !["http:", "https:", "redis:", "postgresql:"].includes(url.protocol) ||
      !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
    )
      throw new Error(`cloud-mode ${name} must be loopback`);
  }
  return env;
}

export function loadCloudModeProfile(overlay, checkout, format = "env") {
  if (process.platform !== "linux")
    throw new Error("cloud-mode profiles are VM-only");
  if (!overlay) throw new Error("cloud-mode requires E2E_CLOUD_MODE_OVERLAY");
  if (!path.isAbsolute(overlay) || !["env", "json"].includes(format))
    throw new Error("invalid cloud-mode overlay or format");
  const dir = realpathSync(overlay),
    root = realpathSync(checkout);
  if (dir === root || dir.startsWith(root + path.sep))
    throw new Error("cloud-mode overlay must be outside the checkout");
  for (
    let parent = dir;
    parent !== path.dirname(parent);
    parent = path.dirname(parent)
  ) {
    if (existsSync(path.join(parent, ".git")))
      throw new Error("cloud-mode overlay must be outside every checkout");
  }
  const file = path.join(dir, `profile.${format}`);
  if (!lstatSync(file).isFile() || realpathSync(file) !== file)
    throw new Error("cloud-mode profile must be a regular non-symlink file");
  const env = parseCloudModeProfile(readFileSync(file, "utf8"), format);
  if (
    env.E2E_CLOUD_MODE_OVERLAY &&
    realpathSync(env.E2E_CLOUD_MODE_OVERLAY) !== dir
  )
    throw new Error("cloud-mode overlay binding mismatch");
  return { ...env, E2E_CLOUD_MODE_OVERLAY: dir };
}
