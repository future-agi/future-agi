/**
 * Maps an attribute inventory's `inventoryControlProps` (the cursor state
 * behind "Load more attributes" / "Retry attributes") onto the panel's
 * catalog props, so the panel's property picker pages and retries the same
 * inventory the old inline controls did.
 */
export const inventoryToCatalogProps = (control) => {
  if (!control) return {};
  const failed = Boolean(
    control.isError ||
      control.isExactSearchError ||
      control.cursorRetryExhausted,
  );
  const retry = control.canRetry === false ? undefined : control.onRetry;
  return {
    hasNextCatalogPage: Boolean(control.hasNextPage),
    catalogContinuationKey: control.continuationKey ?? null,
    isFetchingNextCatalogPage: Boolean(control.isFetchingNextPage),
    catalogError: failed,
    catalogNextPageError: Boolean(control.isFetchNextPageError),
    loadNextCatalogPage: failed
      ? retry || control.onLoadMore
      : control.onLoadMore,
  };
};
