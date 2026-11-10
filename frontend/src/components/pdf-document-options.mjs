// Defense in depth for the patched PDF.js renderer; not a dependency exception.
export const safePdfDocumentOptions = (options) => ({
  ...options,
  isEvalSupported: false,
});
