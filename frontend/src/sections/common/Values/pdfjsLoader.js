import { importWithRecovery } from "src/utils/chunkRecovery";

// Cache dependencies, not recovery: a second preview on a newer route may
// subscribe to the same pending import and must keep its own route ownership.
export function createPdfjsLoader(importModules) {
  let pdfjsPromise = null;
  return () =>
    importWithRecovery(() => {
      if (!pdfjsPromise)
        pdfjsPromise = importModules()
          .then(([lib, worker]) => {
            lib.GlobalWorkerOptions.workerSrc = worker.default;
            return lib;
          })
          .catch((error) => {
            // An abandoned/failed import must not poison later preview attempts.
            pdfjsPromise = null;
            throw error;
          });
      return pdfjsPromise;
    });
}

export const loadPdfjs = createPdfjsLoader(() =>
  Promise.all([
    import("pdfjs-dist"),
    import("pdfjs-dist/build/pdf.worker.min.js?url"),
  ]),
);
