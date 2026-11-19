import { useCallback, useRef } from "react";
import {
  BUILD_FILES_ACCEPT,
  extOf,
} from "src/sections/simulate/environments/buildDrawer/forms/shared/buildFiles";

// Stub of the design documents upload (#3612): one request per file, answered
// with { id, name, content_type, sha256, size }. Nothing is sent; see
// UI_ONLY_FEATURES.designDocuments. The real refusals are document_too_large,
// document_unsupported, and design_documents_duplicate_name at design start.
export const DESIGN_DOCUMENT_MAX_BYTES = 25 * 1024 * 1024;

const TICK_MS = 100;

const refusal = (code) => Object.assign(new Error(code), { code });

const fakeSha = (file) =>
  Array.from(`${file.name}:${file.size}:${file.lastModified}`)
    .reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7)
    .toString(16)
    .padStart(64, "0");

/**
 * Returns `upload(file, { onProgress, signal })`: progress 0 to 100 over 1 to
 * 2 s by size, then the stored document, or a rejection with a `code`:
 * too_large, unsupported, or upload_failed (a name with "fail" in it fails on
 * its first try only, so Retry works). An abort rejects with code "aborted".
 */
export function useUploadDesignDocument() {
  const tried = useRef(new Set());
  return useCallback(
    (file, { onProgress, signal } = {}) =>
      new Promise((resolve, reject) => {
        if (file.size > DESIGN_DOCUMENT_MAX_BYTES)
          return reject(refusal("too_large"));
        if (!BUILD_FILES_ACCEPT.split(",").includes(`.${extOf(file.name)}`))
          return reject(refusal("unsupported"));
        const failsNow =
          /fail/i.test(file.name) && !tried.current.has(file.name);
        tried.current.add(file.name);
        const total = 1000 + Math.min(1000, file.size / 2000);
        let elapsed = 0;
        const timer = setInterval(() => {
          elapsed += TICK_MS;
          const pct = Math.min(100, Math.round((elapsed / total) * 100));
          if (failsNow && pct >= 40) {
            clearInterval(timer);
            reject(refusal("upload_failed"));
            return;
          }
          onProgress?.(pct);
          if (pct < 100) return;
          clearInterval(timer);
          resolve({
            id: `doc-${fakeSha(file).slice(-12)}`,
            name: file.name,
            content_type: file.type || "application/octet-stream",
            sha256: fakeSha(file),
            size: file.size,
          });
        }, TICK_MS);
        signal?.addEventListener("abort", () => {
          clearInterval(timer);
          reject(refusal("aborted"));
        });
        return undefined;
      }),
    [],
  );
}
