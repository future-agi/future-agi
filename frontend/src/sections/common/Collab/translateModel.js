// W4-8 (D23): translate a Row view section, pure. Every language,
// model and count comes from the translate read (`GET …/translate/`).

const nameOf = (languages, code) =>
  (languages ?? []).find((l) => l.code === code)?.name ?? code;

/** The section's fields the server knows, by column name. */
export const sectionFields = (fields, names) =>
  (fields ?? []).filter((f) => (names ?? []).includes(f.name));

/** The section's language: the most common detected language of its fields. */
export function sectionLanguage(fields) {
  const counts = {};
  (fields ?? []).forEach((f) => {
    if (f.language) counts[f.language] = (counts[f.language] ?? 0) + 1;
  });
  return Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

/**
 * Targets a section offers: any target at least one of its fields offers,
 * `[{code, name, fields: [column_id]}]`, English first when the section is
 * not English, then in the server's language order.
 */
export function sectionTargets(fields, languages) {
  const order = (languages ?? []).map((l) => l.code);
  const byCode = new Map();
  (fields ?? []).forEach((f) =>
    (f.available ?? []).forEach((code) => {
      if (!byCode.has(code)) byCode.set(code, []);
      byCode.get(code).push(f.column_id);
    }),
  );
  const lang = sectionLanguage(fields);
  const rank = (code) =>
    code === "en" && lang && lang !== "en"
      ? -1
      : order.includes(code)
        ? order.indexOf(code)
        : order.length;
  return [...byCode.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]))
    .map(([code, ids]) => ({
      code,
      name: nameOf(languages, code),
      fields: ids,
    }));
}

/** `customer_message · Spanish → English` */
export const translationTitle = (t, { languages, target }) =>
  `${t.name} · ${nameOf(languages, t.source_language)} → ${nameOf(languages, target)}`;

/** `Machine translation · gpt-4o-mini · not saved` / `… · cached` */
export const translationStatus = (model, cached) =>
  `Machine translation · ${model ?? "model"} · ${cached ? "cached" : "not saved"}`;

/**
 * `This dataset is mostly English. 47 rows are in other languages` (`` when
 * every row is in the dataset's language or there is no language).
 */
export function otherLanguagesLine(info) {
  const n = info?.other_language_rows?.count ?? 0;
  const lang = info?.dataset_language_name;
  if (!lang || !n) return "";
  return `This dataset is mostly ${lang}. ${n.toLocaleString("en-US")} row${n === 1 ? " is" : "s are"} in other languages`;
}

/** The pill of the grid view of those rows: `Language differs from the dataset`. */
export const OTHER_LANGUAGE_VIEW = "Language differs from the dataset";
