// R4 (lane M): "Scenarios · 8 scenarios × 3 personas" for a scenarios
// dataset, counted from its rows (distinct `scenario` and persona names);
// the template carries no digit literal (§C10).
import { personaOf } from "src/sections/common/Media/mediaModel";

const cellValue = (row, id) => {
  const c = row?.[id];
  return c && typeof c === "object" && "cell_value" in c ? c.cell_value : c;
};

const plural = (n, one, many) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/**
 * @param {{table?: object[], column_config?: {id: string, name: string}[]}} detail
 * @returns {{scenarios: number, personas: number, text: string}|null}
 */
export function scenarioCountsOf(detail) {
  const cols = detail?.column_config ?? [];
  const rows = detail?.table ?? [];
  const sCol = cols.find((c) => c.name === "scenario");
  const pCol = cols.find((c) => c.name === "persona");
  if (!sCol || !rows.length) return null;
  const scenarios = new Set(
    rows.map((r) => String(cellValue(r, sCol.id) ?? "").trim()).filter(Boolean),
  );
  const personas = new Set(
    pCol
      ? rows
          .map((r) => {
            const v = cellValue(r, pCol.id);
            return (
              personaOf(v)?.name ?? (typeof v === "string" ? v.trim() : "")
            );
          })
          .filter(Boolean)
      : [],
  );
  const parts = [plural(scenarios.size, "scenario", "scenarios")];
  if (personas.size) parts.push(plural(personas.size, "persona", "personas"));
  return {
    scenarios: scenarios.size,
    personas: personas.size,
    text: parts.join(" × "),
  };
}
