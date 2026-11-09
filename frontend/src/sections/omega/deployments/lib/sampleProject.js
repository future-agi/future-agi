// The sample project the New deployment page offers to someone with no project
// yet (parity gap 8, the W0 floor): a support-triage workflow that needs no
// model key and deploys as it is.
//
// Its files live in ../sample/project and are read here as text, so the
// download and "Use the sample" are built by the same code that bundles a
// picked folder (./tar.js, ./bundle.js): the archive passes the page's own
// checks, and EE's (pipeline/bundle.ts), by construction.

import manifest from "../sample/project/omega.studio.json?raw";
import readme from "../sample/project/README.md?raw";
import triage from "../sample/project/entries/triage.ts?raw";

import {
  compareBundlePaths,
  gzip,
  PROJECT_MANIFEST,
  readBundleArchive,
} from "./bundle";
import { writeTar } from "./tar";

export const SAMPLE_FILE_NAME = "omega-sample-support-triage.tar.gz";

/** The sample's files, in the order `omega deploy` would bundle them. */
export const SAMPLE_FILES = Object.freeze(
  [
    { path: PROJECT_MANIFEST, text: manifest },
    { path: "README.md", text: readme },
    { path: "entries/triage.ts", text: triage },
  ].sort((a, b) => compareBundlePaths(a.path, b.path)),
);

const encoder = new TextEncoder();

/** The sample as a .tar.gz: omega.studio.json at its root, files only. */
export async function sampleArchive() {
  const tar = writeTar(
    SAMPLE_FILES.map((file) => ({
      path: file.path,
      data: encoder.encode(file.text),
      executable: false,
    })),
  );
  return gzip(tar);
}

/** The sample as a File, exactly the bytes the download saves. */
export async function sampleFile() {
  const archive = await sampleArchive();
  return new File([archive], SAMPLE_FILE_NAME, { type: "application/gzip" });
}

/** The sample read the way a picked .tar.gz is read: a bundle ready to deploy. */
export async function sampleBundle() {
  return readBundleArchive(await sampleFile());
}

/** Save the sample to the person's downloads. */
export async function downloadSample(doc = document) {
  const file = await sampleFile();
  const url = URL.createObjectURL(file);
  try {
    const link = doc.createElement("a");
    link.href = url;
    link.download = SAMPLE_FILE_NAME;
    link.rel = "noopener";
    doc.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    // Revoked after the click has been handled, so the save is not cut short.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
