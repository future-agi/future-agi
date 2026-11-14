// Preserve document options while disabling PDF.js evaluation.
export const safePdfDocumentOptions = (options) => ({
  ...options,
  isEvalSupported: false,
});
