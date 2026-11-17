/**
 * One check job, end to end (SPEC §4.2): unpack, manifest, licence, secrets, dependencies, static
 * scan, sandbox run, capabilities and disclosure facts, diff and similarity. Pure apart from the
 * sandbox and the two registry callbacks, so tests can run it without Django.
 */
import { readUpload } from "./archive.mjs";
import { dependencyFindings } from "./deps.mjs";
import { diffVersions } from "./diff.mjs";
import { statusOf } from "./findings.mjs";
import { licenceFindings } from "./licence.mjs";
import { capabilities, disclosureGaps, observedFacts } from "./observed.mjs";
import { catalogDigest, packCanonical } from "./pack.mjs";
import { registryRules, vendoredRules } from "./registry-rules.mjs";
import { scanEntry } from "./scan.mjs";
import { secretFindings } from "./secrets.mjs";
import { similarityFindings } from "./similarity.mjs";
import { unpack } from "./unpack.mjs";

export const STEPS = ["unpack", "manifest", "licence", "secrets", "dependencies", "static-scan", "sandbox-run", "capabilities", "diff", "similarity"];

const timed = (id, run) => {
  const started = Date.now();
  const findings = run();
  return { id, status: statusOf(findings), ms: Date.now() - started, findings };
};
const prefixed = (unpacked) => new Map([...unpacked.entry, ...[...unpacked.sourceFiles].map(([rel, data]) => [`source/${rel}`, data])]);

function previousOf(bundle) {
  if (!bundle) return null;
  const files = new Map();
  for (const member of readUpload(bundle).members) {
    if (member.type === "file" && member.name.startsWith("entry/")) files.set(member.name.slice(6), member.data);
  }
  const manifest = JSON.parse(files.get("omega.hub.json").toString("utf8"));
  const scan = scanEntry(files, manifest);
  return { files, manifest, version: manifest.version, observed: observedFacts(scan.facts, manifest), flags: scan.findings.filter((item) => item.severity === "flag") };
}

/**
 * `job`: the claim. `raw`: the upload. `registry`: `{ context(body), previousBundle() }`.
 * `sandbox(args)`: runs the keyless test and demo, returns `{ step, log }`.
 * Returns `{ report, bundle, log }`; `bundle` is null when unpacking failed.
 */
export async function checkSubmission({ job, raw, registry, sandbox, checker }) {
  const steps = [];
  const done = (outcome, extra = {}) => ({
    report: {
      schema: "omega-hub-check-report/v1", job: job.job, submission: job.submission, checker,
      manifest: extra.manifest ?? null, digest: extra.digest ?? null, bundleSha256: extra.bundleSha256 ?? null,
      bundleBytes: extra.bundleBytes ?? null,
      steps: [...steps, ...STEPS.filter((id) => !steps.some((step) => step.id === id)).map((id) => ({ id, status: "skipped", ms: 0, findings: [] }))],
      outcome,
    },
    bundle: extra.bundle ?? null,
    log: extra.log ?? "",
  });
  const unpacked = unpack(raw, { tier: job.publisherTier });
  steps.push({ id: "unpack", status: statusOf(unpacked.findings), ms: 0, findings: unpacked.findings, notes: unpacked.notes });
  if (unpacked.findings.length) return done("checks_failed");
  const manifest = JSON.parse(unpacked.manifestRaw.toString("utf8"));
  const context = await registry.context({ name: unpacked.id, version: manifest.version ?? null, remixOf: manifest.remix_of ?? null });
  const vendored = vendoredRules(unpacked, manifest);
  steps.push(timed("manifest", () => [...vendored.manifest, ...registryRules(manifest, { tier: job.publisherTier, hints: job.hints, context })]));
  steps.push(timed("licence", () => [...vendored.licence, ...licenceFindings(manifest, unpacked.entry)]));
  const all = prefixed(unpacked);
  steps.push(timed("secrets", () => secretFindings(all)));
  steps.push(timed("dependencies", () => dependencyFindings(all)));
  let scan;
  steps.push(timed("static-scan", () => {
    scan = scanEntry(all, manifest);
    return scan.findings;
  }));
  const packed = packCanonical(unpacked.entry, unpacked.sourceFiles);
  const base = { manifest: { id: unpacked.id, version: manifest.version ?? null, kind: manifest.kind ?? null },
    digest: catalogDigest(unpacked.entry, unpacked.sourceFiles, manifest), ...packed };
  const blocked = steps.some((step) => step.status === "fail");
  let log = "";
  if (blocked) {
    steps.push({ id: "sandbox-run", status: "skipped", ms: 0, findings: [], network: "none", why: "an earlier step failed" });
  } else {
    const ran = await sandbox({ jobId: job.job, id: unpacked.id, source: unpacked.source ?? "", bundle: packed.bundle, manifest });
    steps.push(ran.step);
    log = ran.log;
  }
  const observed = observedFacts(scan.facts, manifest);
  steps.push({ ...timed("capabilities", () => disclosureGaps(manifest.disclosure, observed)), result: capabilities(unpacked.entry, manifest), observed });
  let previous = null;
  if (context.previousVersion) previous = previousOf(await registry.previousBundle());
  const diff = diffVersions(previous, { files: unpacked.entry, manifest, observed, flags: scan.findings.filter((item) => item.severity === "flag") });
  steps.push({ id: "diff", status: "pass", ms: 0, findings: [], previous: previous?.version ?? null, diff });
  const ownRef = `${job.publisher}/${unpacked.id}`;
  steps.push(timed("similarity", () => (previous ? [] : similarityFindings(all, { codeHashes: context.codeHashes, ownRef, remixOf: manifest.remix_of }))));
  const outcome = steps.some((step) => step.status === "fail") ? "checks_failed" : "in_review";
  return done(outcome, { ...base, log });
}
