// v2.1 g8 (dyncol:api-calls): Configure reopens an API Calls column with
// the form it was saved with. The column stores the submitted form
// (`{column_name, config: {url, params: {k: {type, value}}, …},
// concurrency}`, column ids in place of names); `toForm` is the one
// transform back to the editor's shape (transformDynamicColumnConfig).
// Pure.

/** The editor values of a saved API Calls column, or null for another shape. */
export const savedApiCallForm = (meta, toForm) => {
  const c = meta?.config;
  if (!c || typeof c !== "object" || Array.isArray(c) || !("url" in c))
    return null;
  const out = toForm({ ...c, concurrency: meta.concurrency });
  return {
    ...out,
    column_name: meta.column_name ?? meta.new_column_name ?? meta.name ?? "",
    concurrency:
      meta.concurrency == null || meta.concurrency === ""
        ? out.concurrency ?? ""
        : meta.concurrency,
  };
};
