import { parsePinnedHubRef } from "../../hub/lib/pinnedRef";
import {
  BundleError,
  compareBundlePaths,
  exclusionFor,
  gzip,
  sha256Hex,
  LANGGRAPH_PROJECT_FILE,
  MAX_BUNDLE_BYTES,
  PROJECT_MANIFEST,
  readLangGraphConfig,
  readManifest,
} from "./bundle";
import {
  checkHubAbort,
  HUB_LIMITS,
  inflateHubArchive,
  readHubTar,
} from "./hubArchive";
import { writeTar } from "./tar";

export { parseHubSource } from "../../hub/lib/pinnedRef";
const fail = (message) => {
  throw new BundleError(message);
};
const hex = /^[a-f0-9]{64}$/;

/** New deployments reject even exact yanked pins; other Hub uses retain their own policy. */
export function validateHubVersion(pin, metadata) {
  if (!metadata || metadata.ref !== pin.ref || metadata.version !== pin.version)
    fail("The Hub response does not match the requested identity and version.");
  if (metadata.status !== "approved")
    fail(
      "This Hub version is missing, yanked or withdrawn. Upload a project manually instead.",
    );
  if (metadata.capabilities?.deploy !== true)
    fail("This Hub version does not support deployment.");
  if (
    typeof metadata.digest !== "string" ||
    !/^sha256:[a-f0-9]{64}$/.test(metadata.digest) ||
    typeof metadata.bundleSha256 !== "string" ||
    !hex.test(metadata.bundleSha256)
  )
    fail("The Hub version has invalid digests.");
  if (
    !Number.isSafeInteger(metadata.bundleBytes) ||
    metadata.bundleBytes <= 0 ||
    metadata.bundleBytes > HUB_LIMITS.compressedBytes
  )
    fail("The Hub archive size exceeds the limit or is missing.");
  if (
    metadata.manifest?.id !== pin.name ||
    metadata.manifest?.version !== pin.version
  )
    fail("The Hub manifest identity does not match the pin.");
  return metadata;
}

// JS code-unit sorting differs from Python's code-point sorting for astral paths.
function codePointOrder(a, b) {
  const left = Array.from(a, (c) => c.codePointAt(0));
  const right = Array.from(b, (c) => c.codePointAt(0));
  for (let i = 0; i < Math.min(left.length, right.length); i += 1)
    if (left[i] !== right[i]) return left[i] - right[i];
  return left.length - right.length;
}

/** Canonical archive digests are checked before decoding; catalog before any deployment exclusions. */
export async function adaptHubBundle({
  pin,
  metadata,
  archive,
  bundleSha256,
  signal,
}) {
  checkHubAbort(signal);
  const exact = parsePinnedHubRef(pin?.ref);
  validateHubVersion(exact, metadata);
  if (
    !(archive instanceof Uint8Array) ||
    archive.length !== metadata.bundleBytes ||
    archive.length > HUB_LIMITS.compressedBytes
  )
    fail("The Hub archive size does not match its metadata.");
  if (bundleSha256 !== undefined && bundleSha256 !== metadata.bundleSha256)
    fail("The Hub archive digest header does not match its metadata.");
  if ((await sha256Hex(archive)) !== metadata.bundleSha256)
    fail("The Hub archive digest does not match.");
  checkHubAbort(signal);
  const members = readHubTar(await inflateHubArchive(archive, signal), signal);
  let manifest;
  try {
    const bytes = members.find((m) => m.path === "entry/omega.hub.json")?.data;
    if (!bytes) fail("Missing entry/omega.hub.json.");
    manifest = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    );
  } catch {
    fail("The Hub manifest could not be read.");
  }
  if (
    !manifest ||
    Array.isArray(manifest) ||
    manifest.id !== exact.name ||
    manifest.version !== exact.version ||
    manifest.source !== metadata.manifest.source
  )
    fail(
      "The Hub archive manifest does not match the requested identity/version/source.",
    );
  const root =
    manifest.source && members.some((m) => m.path.startsWith("source/"))
      ? "source"
      : "entry";
  const selected = members
    .filter((m) => m.path.startsWith(`${root}/`))
    .map((m) => ({
      path: m.path.slice(root.length + 1),
      data: m.data,
      executable: false,
    }));
  const cast =
    typeof manifest.demo?.cast === "string" ? manifest.demo.cast : "demo.cast";
  const lines = [];
  for (const member of selected) {
    checkHubAbort(signal);
    if (root !== "entry" || member.path !== cast)
      lines.push(`${member.path}\0${await sha256Hex(member.data)}`);
  }
  const digest = `sha256:${await sha256Hex(new TextEncoder().encode(lines.sort(codePointOrder).join("\n")))}`;
  if (digest !== metadata.digest)
    fail("The Hub catalog digest does not match the selected source.");
  const included = [];
  const excluded = [];
  for (const member of selected) {
    const reason = exclusionFor(member.path);
    if (reason) excluded.push({ path: member.path, reason });
    else included.push(member);
  }
  included.sort((a, b) => compareBundlePaths(a.path, b.path));
  const project = included.find((m) => m.path === PROJECT_MANIFEST);
  const langgraph = included.find((m) => m.path === LANGGRAPH_PROJECT_FILE);
  if (!project && !langgraph)
    fail(
      "This Hub source has no supported root project manifest. Upload a project manually instead.",
    );
  const summary = project
    ? readManifest(project.data)
    : readLangGraphConfig(langgraph.data, exact.name);
  checkHubAbort(signal);
  const tar = writeTar(included);
  const deploymentArchive = await gzip(tar);
  checkHubAbort(signal);
  if (deploymentArchive.length > MAX_BUNDLE_BYTES)
    fail("The converted deployment exceeds the archive size limit.");
  const sha256 = await sha256Hex(deploymentArchive);
  const contentSha256 = await sha256Hex(tar);
  checkHubAbort(signal);
  return {
    source: "hub",
    archive: deploymentArchive,
    sha256,
    contentSha256,
    size: deploymentArchive.length,
    files: included.map((m) => ({ path: m.path, size: m.data.length })),
    excluded: excluded.sort((a, b) => compareBundlePaths(a.path, b.path)),
    manifest: summary,
    provenance: {
      ref: exact.ref,
      version: exact.version,
      digest,
      bundleSha256: metadata.bundleSha256,
    },
  };
}
