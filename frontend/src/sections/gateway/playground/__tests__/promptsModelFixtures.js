// Synthetic catalogs matching the audited positions, not production model data.
export const laterPageCatalogs = [
  { id: "azure/gpt-4", position: 15, count: 20 },
  { id: "ollama/llama3", position: 37, count: 40 },
].map(({ id, position, count }) => {
  const slash = id.indexOf("/");
  const bare = id.slice(slash + 1);
  const exact = {
    model_name: id,
    providers: id.slice(0, slash),
    is_available: false,
    type: "chat",
  };
  const rows = Array.from({ length: count }, (_, index) =>
    index === position - 1
      ? exact
      : {
          ...exact,
          model_name: `other/${bare}-variant-${index}`,
          providers: "other",
          is_available: true,
        },
  );
  return { id, position, exact, rows };
});

// Model-list API contract: name selects at most one exact name; search is
// substring matching with ten rows per page. This makes the old callers fail.
export function modelListResponse(rows, { params = {} } = {}) {
  const filtered = params.name
    ? rows.filter(
        (row) => row.model_name.toLowerCase() === params.name.toLowerCase(),
      ).slice(0, 1)
    : rows.filter((row) =>
        row.model_name.toLowerCase().includes((params.search || "").toLowerCase()),
      );
  const page = params.page || 1;
  const limit = params.limit || 10;
  return {
    data: {
      results: filtered.slice((page - 1) * limit, page * limit),
      count: filtered.length,
      next: page * limit < filtered.length ? `?page=${page + 1}` : null,
    },
  };
}
