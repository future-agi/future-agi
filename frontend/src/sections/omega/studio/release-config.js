// The two values a release sets for the Studio tab. Runtime config
// (window.__FUTURE_AGI_CONFIG__, written by frontend/docker-entrypoint.sh)
// wins over the build-time value, as in ../shell/flags.js and
// src/config-global.js.
//
// VITE_OMEGA_CLI_PACKAGE: the npm package that installs the Omega CLI and its
//   `omega dev` (ruling 711, owner 2026-10-27: the @future-agi npm scope, so
//   `@future-agi/omega`; kept as this one value so it can change). The install step's command is built from it and only
//   from it: `npm install -g <package>`. Unset or unreadable, the step says
//   that this build names no package (StudioInstallStep.jsx).
// VITE_OMEGA_STUDIO_DOCS_URL: the public Studio guide (docs.futureagi.com
//   /studio once the docs release ships it). Set only once it resolves; the
//   tab links to it then, and never to a page that may not exist.

export const CLI_PACKAGE_KEY = "VITE_OMEGA_CLI_PACKAGE";
export const STUDIO_DOCS_URL_KEY = "VITE_OMEGA_STUDIO_DOCS_URL";

// An npm package name, scoped or not, with an optional exact version or
// dist-tag. Nothing else (spaces, shell characters, a URL, a path, a git
// spec) can reach the copyable command.
const NPM_NAME = /^(?:@[a-z0-9][a-z0-9._-]*\/)?[a-z0-9][a-z0-9._-]*$/;
const NPM_VERSION = /^[A-Za-z0-9][A-Za-z0-9.+_-]*$/;
const MAX_NAME_LENGTH = 214;

export function readReleaseValue(name, runtimeConfig, buildEnv) {
  const runtimeValue = runtimeConfig?.[name];
  const raw =
    runtimeValue !== undefined && runtimeValue !== null && runtimeValue !== ""
      ? runtimeValue
      : buildEnv?.[name];
  return typeof raw === "string" ? raw.trim() : "";
}

function releaseValue(name) {
  const runtimeConfig =
    (typeof window !== "undefined" && window.__FUTURE_AGI_CONFIG__) || {};
  let buildEnv = {};
  try {
    buildEnv = import.meta.env || {};
  } catch {
    buildEnv = {};
  }
  return readReleaseValue(name, runtimeConfig, buildEnv);
}

/** `name` or `name@version` when the value is a plain npm package spec, else null. */
export function cliPackageSpec(value) {
  if (typeof value !== "string" || value === "") return null;
  const at = value.lastIndexOf("@");
  const [name, version] =
    at > 0 ? [value.slice(0, at), value.slice(at + 1)] : [value, null];
  if (name.length > MAX_NAME_LENGTH || !NPM_NAME.test(name)) return null;
  if (version !== null && !NPM_VERSION.test(version)) return null;
  return value;
}

/** `npm install -g <package>` once the release names the CLI package, else null. */
export function cliInstallCommand(value = releaseValue(CLI_PACKAGE_KEY)) {
  const spec = cliPackageSpec(value);
  return spec ? `npm install -g ${spec}` : null;
}

/** The Studio guide once the release sets its address (https only), else null. */
export function studioDocsUrl(value = releaseValue(STUDIO_DOCS_URL_KEY)) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url.toString();
  } catch {
    return null;
  }
}
