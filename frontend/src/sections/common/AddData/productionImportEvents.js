export const PRODUCTION_IMPORT_READY = "dataset-observe-import-ready";

export function notifyProductionImport(datasetId) {
  window.dispatchEvent(
    new CustomEvent(PRODUCTION_IMPORT_READY, { detail: { datasetId } }),
  );
}
