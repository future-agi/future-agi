/* eslint-env node */
// Small client for the lane's real Hub registry (scripts/omega-hub/dev-up.sh):
// sign-in, Hub-token uploads, polling and folder packing. Used by the e2e
// seed and the Playwright run; never by the app.
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO = path.resolve(HERE, "../../../../../../..");
export const BASE = process.env.HUB_E2E_BACKEND || "http://127.0.0.1:8741";
export const API = `${BASE}/omega-hub/v1`;
export const STATE = process.env.OMEGA_HUB_LANE_STATE || "/tmp/hubfe-lane";
export const PASSWORD = process.env.OMEGA_HUB_DEV_PASSWORD || "omega-hub-dev-password";

const { writeTarGz } = await import(
  path.join(REPO, "services/omega-hub-checker/src/archive.mjs")
);

export function seed() {
  // Django's startup logs share stdout with the seed's JSON and can hold "{": parse from the last line
  // that starts with "{", where the pretty-printed summary begins.
  const text = readFileSync(path.join(STATE, "seed.json"), "utf8");
  const starts = [...text.matchAll(/^\{/gm)].map((match) => match.index);
  return JSON.parse(text.slice(starts.at(-1) ?? 0));
}

export function tokenA() {
  return readFileSync(path.join(STATE, "token-a"), "utf8").trim();
}

export async function call(pathname, { method = "GET", headers = {}, body, expect } = {}) {
  const url = pathname.startsWith("http") ? pathname : `${API}${pathname}`;
  const response = await fetch(url, { method, headers, body, redirect: "manual" });
  const text = await response.text();
  if (expect !== undefined && response.status !== expect) {
    throw new Error(`${method} ${pathname}: HTTP ${response.status} (wanted ${expect}): ${text.slice(0, 400)}`);
  }
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text;
  }
}

export async function login(email) {
  const response = await fetch(`${BASE}/accounts/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password: PASSWORD }),
  });
  const body = await response.json().catch(() => ({}));
  const access = body.access ?? body.result?.access ?? body.data?.access;
  const refresh = body.refresh ?? body.result?.refresh ?? body.data?.refresh;
  if (!response.ok || !access) throw new Error(`login ${email}: HTTP ${response.status} ${JSON.stringify(body).slice(0, 300)}`);
  return { access, refresh, headers: { Authorization: `Bearer ${access}` } };
}

function listFolder(dir, prefix = "", out = []) {
  for (const name of readdirSync(dir).sort()) {
    const file = path.join(dir, name);
    if (statSync(file).isDirectory()) listFolder(file, `${prefix}${name}/`, out);
    else out.push({ name: `entry/${prefix}${name}`, data: readFileSync(file) });
  }
  return out;
}

// A .tar.gz of one entry folder, laid out as the canonical bundle expects.
export function packFolder(dir, transform) {
  const files = listFolder(dir);
  return writeTarGz(transform ? transform(files) : files);
}

export async function upload({ headers, publisher, bundle, name, version, channel = "cli", notes }) {
  const form = new FormData();
  form.append("publisher", publisher);
  if (name) form.append("name", name);
  if (version) form.append("version", version);
  form.append("channel", channel);
  if (notes) form.append("notes", notes);
  form.append("bundle", new Blob([bundle], { type: "application/gzip" }), `${name || "entry"}.tar.gz`);
  return call("/submissions", { method: "POST", headers, body: form, expect: 201 });
}

export async function waitForStatus(id, headers, { leave = ["received", "checking"], tries = 200 } = {}) {
  let submission;
  for (let i = 0; i < tries; i += 1) {
    submission = await call(`/submissions/${id}`, { headers });
    if (!leave.includes(submission.status)) return submission;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error(`submission ${id} stayed ${submission?.status}`);
}
