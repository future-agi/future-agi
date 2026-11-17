// Project bundles for `omega-deploy/1`, built in the browser.
//
// Two sources, one result:
//   - a project folder the user picked: every file is read, the same files
//     `omega deploy` leaves out are left out (and listed), and the rest become
//     a deterministic tar (./tar.js) that is gzipped here;
//   - a .tar.gz that `omega deploy bundle --out` wrote: it is inflated and
//     checked, and its own bytes are sent unchanged.
// Either way the result carries what the control plane verifies: the gzip
// bytes, their sha256, the uncompressed tar's sha256 (contentSha256), the
// size and a root omega.studio.json that names at least one entry, or, for a
// LangGraph.js project without one, a root langgraph.json that names at least
// one graph (omega.studio.json wins when both are there, as `omega deploy`
// reads a folder). The control plane checks the rest of langgraph.json (each
// graph's module in the bundle, no setting Omega does not apply) and its
// refusal is shown as it says it.

import { TarError, readTarMembers, writeTar } from "./tar";

export const PROJECT_MANIFEST = "omega.studio.json";
export const LANGGRAPH_PROJECT_FILE = "langgraph.json";
// EE's DEFAULT_BUNDLE_MAX_BYTES: the largest bundle a control plane accepts.
export const MAX_BUNDLE_BYTES = 24 * 1024 * 1024;
const MAX_SOURCE_BYTES = 100 * 1024 * 1024;
const MAX_FILES = 20_000;

export class BundleError extends Error {
  constructor(message, problems = []) {
    super(message);
    this.name = "BundleError";
    this.problems = problems;
  }
}

const EXCLUDED_DIRECTORIES = {
  ".omega": "local state",
  node_modules: "dependencies",
  ".git": "version control",
  ".hg": "version control",
  ".svn": "version control",
};
const SECRET_FILES = new Set([
  ".npmrc",
  ".netrc",
  ".pypirc",
  "id_rsa",
  "id_ecdsa",
  "id_ed25519",
]);

/** Why `omega deploy` never bundles a file of this name, or undefined. */
export function fileExclusion(name) {
  if (name === ".DS_Store" || name === "Thumbs.db") return "system file";
  if ((name === ".env" || name.startsWith(".env.")) && name !== ".env.example")
    return "may hold secrets";
  if (SECRET_FILES.has(name)) return "may hold secrets";
  if (/\.(pem|key|p12|pfx|jks|keystore)$/i.test(name))
    return "may hold secrets";
  return undefined;
}

/** Why a project-relative path is left out of a bundle, or undefined. */
export function exclusionFor(relativePath) {
  const parts = relativePath.split("/");
  for (const part of parts.slice(0, -1)) {
    if (EXCLUDED_DIRECTORIES[part]) return EXCLUDED_DIRECTORIES[part];
  }
  return fileExclusion(parts[parts.length - 1]);
}

/** Directory-walk order: each folder's names in code-unit order, depth first. */
export function compareBundlePaths(a, b) {
  const left = a.split("/");
  const right = b.split("/");
  for (let i = 0; i < Math.min(left.length, right.length); i += 1) {
    if (left[i] !== right[i]) return left[i] < right[i] ? -1 : 1;
  }
  return left.length - right.length;
}

export async function sha256Hex(bytes) {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

async function throughStream(bytes, stream) {
  const piped = new Response(bytes).body.pipeThrough(stream);
  return new Uint8Array(await new Response(piped).arrayBuffer());
}

/** A picked File's bytes (FileReader where Blob#arrayBuffer is missing). */
export function readBytes(file) {
  if (typeof file.arrayBuffer === "function")
    return file.arrayBuffer().then((buffer) => new Uint8Array(buffer));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

export async function gzip(bytes) {
  const out = await throughStream(bytes, new CompressionStream("gzip"));
  out[9] = 0xff; // OS "unknown", as `omega deploy` writes it
  return out;
}

export const gunzip = (bytes) =>
  throughStream(bytes, new DecompressionStream("gzip"));

/** Canonical base64 of bytes (the bundle's `data`). */
export function toBase64(bytes) {
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return globalThis.btoa(binary);
}

/** The project name and entry count from omega.studio.json, or a BundleError. */
export function readManifest(bytes) {
  let parsed;
  try {
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new BundleError(`${PROJECT_MANIFEST} is not valid JSON.`);
  }
  const entries = parsed?.entries;
  if (!parsed || typeof parsed !== "object" || !Array.isArray(entries)) {
    throw new BundleError(`${PROJECT_MANIFEST} has no entries list.`);
  }
  if (entries.length === 0) {
    throw new BundleError(`${PROJECT_MANIFEST} lists no entries.`);
  }
  return {
    file: PROJECT_MANIFEST,
    name: typeof parsed.name === "string" ? parsed.name : null,
    entries: entries.map((entry) => ({
      id: typeof entry?.id === "string" ? entry.id : null,
      label: typeof entry?.label === "string" ? entry.label : null,
      kind: typeof entry?.kind === "string" ? entry.kind : null,
    })),
  };
}

/**
 * The graphs of a LangGraph.js project's langgraph.json, as entries, or a
 * BundleError. `name` is the project's (its folder's name, as `omega deploy`
 * names it), or null when it has none.
 */
export function readLangGraphConfig(bytes, name = null) {
  let parsed;
  try {
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new BundleError(`${LANGGRAPH_PROJECT_FILE} is not valid JSON.`);
  }
  const graphs = parsed?.graphs;
  if (
    !graphs ||
    typeof graphs !== "object" ||
    Array.isArray(graphs) ||
    Object.keys(graphs).length === 0
  ) {
    throw new BundleError(`${LANGGRAPH_PROJECT_FILE} names no graphs.`);
  }
  return {
    file: LANGGRAPH_PROJECT_FILE,
    name: typeof name === "string" && name !== "" ? name : null,
    entries: Object.entries(graphs).map(([id, spec]) => ({
      id,
      label:
        spec && typeof spec === "object" && typeof spec.description === "string"
          ? spec.description
          : null,
      kind: "langgraph",
    })),
  };
}

/**
 * The project's summary from its root files (`read(name)` gives a root file's
 * bytes or undefined), or `undefined` when it has neither project file.
 */
function projectSummary(read, name) {
  const manifest = read(PROJECT_MANIFEST);
  if (manifest !== undefined) return readManifest(manifest);
  const langgraph = read(LANGGRAPH_PROJECT_FILE);
  if (langgraph !== undefined) return readLangGraphConfig(langgraph, name);
  return undefined;
}

/** `omega deploy`'s default deployment name for a project name. */
export function deploymentNameFrom(projectName) {
  const cleaned = String(projectName || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/^[^a-z]+/, "");
  return (cleaned === "" ? "omega-project" : cleaned)
    .slice(0, 40)
    .replace(/-+$/, "");
}

async function finish({ archive, tar, files, excluded, manifest, source }) {
  if (archive.length > MAX_BUNDLE_BYTES) {
    throw new BundleError(
      `The bundle is ${formatBytes(archive.length)}; a deployment accepts at most ${formatBytes(MAX_BUNDLE_BYTES)}. A bundle holds source, not data.`,
    );
  }
  const [sha256, contentSha256] = await Promise.all([
    sha256Hex(archive),
    sha256Hex(tar),
  ]);
  return {
    source,
    archive,
    sha256,
    contentSha256,
    size: archive.length,
    files,
    excluded,
    manifest,
  };
}

/**
 * Bundle a picked folder. `picked` is a FileList/array from an
 * <input webkitdirectory>: each File's webkitRelativePath starts with the
 * folder's own name, which is dropped so omega.studio.json lands at the root.
 */
export async function bundleFolder(picked) {
  const list = Array.from(picked || []);
  if (list.length === 0) throw new BundleError("The folder is empty.");
  const included = [];
  const excluded = [];
  let total = 0;
  for (const file of list) {
    const full = file.webkitRelativePath || file.name;
    const relative = full.split("/").slice(1).join("/") || file.name;
    const reason = exclusionFor(relative);
    if (reason) {
      excluded.push({ path: relative, reason });
      continue;
    }
    total += file.size;
    if (total > MAX_SOURCE_BYTES)
      throw new BundleError(
        "The project is larger than 100 MB without its excluded files; a bundle holds source, not data.",
      );
    included.push({ relative, file });
  }
  if (included.length > MAX_FILES)
    throw new BundleError(`The project has more than ${MAX_FILES} files.`);
  included.sort((a, b) => compareBundlePaths(a.relative, b.relative));
  const projectFile = included.find(
    (item) =>
      item.relative === PROJECT_MANIFEST ||
      item.relative === LANGGRAPH_PROJECT_FILE,
  );
  if (!projectFile) {
    throw new BundleError(
      `This folder has no ${PROJECT_MANIFEST} at its top level, so it is not an Omega project. Pick the folder that holds it.`,
    );
  }
  const entries = await Promise.all(
    included.map(async ({ relative, file }) => ({
      path: relative,
      data: await readBytes(file),
      executable: false,
    })),
  );
  const folderName = (list[0].webkitRelativePath || "").split("/")[0];
  const manifest = projectSummary(
    (name) => entries.find((entry) => entry.path === name)?.data,
    folderName,
  );
  let tar;
  try {
    tar = writeTar(entries);
  } catch (error) {
    if (error instanceof TarError) throw new BundleError(error.message);
    throw error;
  }
  const archive = await gzip(tar);
  return finish({
    archive,
    tar,
    files: entries.map((entry) => ({
      path: entry.path,
      size: entry.data.length,
    })),
    excluded: excluded.sort((a, b) => compareBundlePaths(a.path, b.path)),
    manifest,
    source: "folder",
  });
}

/** Check a .tar.gz from `omega deploy bundle --out`; its bytes are sent as they are. */
export async function readBundleArchive(file) {
  if (!file) throw new BundleError("Choose a .tar.gz bundle.");
  if (file.size > MAX_BUNDLE_BYTES) {
    throw new BundleError(
      `The bundle is ${formatBytes(file.size)}; a deployment accepts at most ${formatBytes(MAX_BUNDLE_BYTES)}.`,
    );
  }
  const archive = await readBytes(file);
  let tar;
  try {
    tar = await gunzip(archive);
  } catch {
    throw new BundleError("This file is not a gzip-compressed tar archive.");
  }
  let members;
  try {
    members = readTarMembers(tar);
  } catch (error) {
    if (error instanceof TarError) throw new BundleError(error.message);
    throw error;
  }
  const manifest = projectSummary(
    (name) => members.find((m) => m.path === name)?.data,
    null,
  );
  if (!manifest) {
    throw new BundleError(
      `This archive has no ${PROJECT_MANIFEST} at its root, so it is not an Omega project bundle.`,
    );
  }
  return finish({
    archive,
    tar,
    files: members.map((m) => ({ path: m.path, size: m.data.length })),
    excluded: [],
    manifest,
    source: "archive",
  });
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
