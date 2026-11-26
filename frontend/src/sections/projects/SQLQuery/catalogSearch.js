// Preserve the catalog's exact types and descriptions; search only selects rows.
export function searchCatalog(datasets = [], search = "") {
  const terms = search.toLowerCase().trim().split(/\s+/).filter(Boolean);
  return datasets.flatMap((dataset) => {
    const context =
      `${dataset.name} ${dataset.description || ""}`.toLowerCase();
    const columns = dataset.columns.filter((column) =>
      terms.every((term) =>
        `${context} ${column.name} ${column.type}`.toLowerCase().includes(term),
      ),
    );
    return columns.length || terms.every((term) => context.includes(term))
      ? [{ ...dataset, columns, totalColumns: dataset.columns.length }]
      : [];
  });
}
