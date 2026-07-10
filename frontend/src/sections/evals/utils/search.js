export const normalizeEvalSearchText = (searchText) =>
  String(searchText ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
