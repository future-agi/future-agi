// CSV for one analytics widget: a header from every key the rows carry, then
// one line per row. Missing values are empty cells, never "-".
export function toCsv(rows = []) {
  const columns = [
    ...rows.reduce((keys, row) => {
      Object.keys(row || {}).forEach((key) => keys.add(key));
      return keys;
    }, new Set()),
  ];
  const cell = (value) => {
    if (value == null) return "";
    const text = String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [
    columns.join(","),
    ...rows.map((row) => columns.map((key) => cell(row?.[key])).join(",")),
  ].join("\n");
}

export const csvFilename = (title) =>
  `${
    String(title || "widget")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "widget"
  }.csv`;

export function downloadCsv(filename, rows) {
  if (!rows?.length) return;
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
