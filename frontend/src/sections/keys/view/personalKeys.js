// Lists never expose credential material, including administrative member views.
export function maskedKey(value) {
  const text = String(value || "");
  if (!text) return "—";
  if (text.includes("*")) return text;
  return "********";
}

export function personalKeyRows(rows = []) {
  return rows
    .filter((row) => row.type === "user")
    .map((row) => ({
      ...row,
      api_key: maskedKey(row.masked_api_key ?? row.api_key),
      secret_key: maskedKey(row.masked_secret_key ?? row.secret_key),
    }));
}
