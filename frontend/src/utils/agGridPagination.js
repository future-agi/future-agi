export const getZeroBasedGridPage = (request, fallbackPageSize) => {
  const startRow = Number.isInteger(request?.startRow)
    ? Math.max(request.startRow, 0)
    : 0;
  const requestedPageSize =
    Number.isInteger(request?.endRow) && request.endRow > startRow
      ? request.endRow - startRow
      : null;
  const pageSize = requestedPageSize || fallbackPageSize;

  if (!Number.isInteger(pageSize) || pageSize < 1) {
    throw new Error("AG Grid page size must be a positive integer");
  }

  return {
    pageNumber: Math.floor(startRow / pageSize),
    pageSize,
  };
};
