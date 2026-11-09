const integer = (value) => {
  if (value == null || String(value).trim() === "") return null;
  if (!/^\d+$/.test(String(value)) || !Number.isSafeInteger(Number(value))) {
    throw new Error(
      "Use a whole number of calls, zero to block, or leave blank for no limit.",
    );
  }
  return Number(value);
};

export const limitsRows = (budgets) =>
  Object.entries(budgets?.per_tool || {}).map(([pattern, rule]) => ({
    pattern,
    rpm: rule?.rpm == null ? "" : String(rule.rpm),
    calls:
      rule?.max_calls_per_run == null ? "" : String(rule.max_calls_per_run),
  }));

export function limitsPatch(rpm, calls, rows, stored = {}) {
  const perTool = Object.fromEntries(
    Object.keys(stored?.per_tool || {}).map((pattern) => [pattern, null]),
  );
  const seen = new Set();
  rows.forEach((row) => {
    const pattern = row.pattern.trim();
    if (!pattern || seen.has(pattern))
      throw new Error("Give each tool rule a different, nonempty pattern.");
    seen.add(pattern);
    const rate = integer(row.rpm);
    const count = integer(row.calls);
    perTool[pattern] =
      rate == null && count == null
        ? null
        : { rpm: rate, max_calls_per_run: count };
  });
  return {
    rpm_per_key: integer(rpm),
    max_tool_calls_per_run: integer(calls),
    per_tool: perTool,
  };
}
