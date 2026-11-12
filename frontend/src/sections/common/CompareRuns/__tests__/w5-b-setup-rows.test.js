// W5 B6: task compare's Setup rows come from the one config
// diff. The changes it counts and lists are exactly configRows(a, b) for a
// pair (Explore › Config); unchanged settings are context rows only.
import { describe, expect, it } from "vitest";
import { configRows } from "../configRows";
import { settingName, setupRows } from "../setupRows";

const config = {
  kind: "prompt",
  model: {
    id: "claude-sonnet-4-5",
    params: { temperature: 0.2, max_tokens: 400 },
  },
  prompt: {
    id: "p1",
    name: "Support Reply",
    version: "V4",
    messages: [{ role: "system", content: "Be brief." }],
  },
  tools: [{ name: "lookup_order" }],
  dataset: { version: "v5", rows: 238 },
  evals: [{ id: "groundedness", name: "Groundedness", version: "v2" }],
  trials: 1,
};
const run = (label, patch = {}) => ({
  run_id: label,
  label,
  config: { ...config, ...patch },
});
const info = (r) => [
  ["Model", r.config.model.id],
  ["Tools", (r.config.tools ?? []).map((t) => t.name).join(", ")],
];
const col = (r) => ({ run: r, info: info(r) });
const changed = (m) => m.rows.filter((r) => r.changed);

const base = run("#2A");
const maxTokens = run("#2B", {
  model: { ...config.model, params: { temperature: 0.2, max_tokens: 800 } },
});
const many = run("#7B", {
  model: {
    id: "gpt-4o-mini",
    params: { temperature: 0.9, max_tokens: 400, response_format: "json" },
  },
  prompt: { ...config.prompt, version: "V5" },
  tools: [{ name: "lookup_order" }, { name: "search_kb_v2" }],
  evals: [{ id: "groundedness", name: "Groundedness", version: "v3" }],
  trials: 3,
  limits: { timeout_s: 30 },
});

describe("setupRows", () => {
  it("max tokens only: 1 difference, as Explore › Config counts it", () => {
    const m = setupRows([col(base), col(maxTokens)]);
    expect(m.count).toBe(configRows(base, maxTokens).count);
    expect(m.count).toBe(1);
    expect(changed(m)).toEqual([
      expect.objectContaining({
        name: "Max tokens",
        values: ["400", "800"],
        differs: [false, true],
      }),
    ]);
    // the unchanged settings stay as context, never bold
    const model = m.rows.find((r) => r.name === "Model");
    expect(model).toMatchObject({ changed: false, differs: [false, false] });
    expect(m.rows.find((r) => r.name === "Temperature").values).toEqual([
      "0.2",
      "0.2",
    ]);
  });

  it("the count always equals configRows(a, b).count, row for row", () => {
    [maxTokens, many, base].forEach((b) => {
      const m = setupRows([col(base), col(b)]);
      const want = configRows(base, b);
      expect(m.count).toBe(want.count);
      expect(changed(m)).toHaveLength(want.chips.length);
    });
    const m = setupRows([col(base), col(many)]);
    expect(changed(m).map((r) => r.name)).toEqual(
      expect.arrayContaining([
        "Model",
        "Temperature",
        "Response format",
        "Prompt version",
        "Tools",
        "Evaluations",
      ]),
    );
    expect(changed(m).find((r) => r.name === "Evaluations").values).toEqual([
      "groundedness v2",
      "groundedness v3",
    ]);
    expect(changed(m).find((r) => r.name === "Prompt version").values).toEqual([
      "V4",
      "V5",
    ]);
  });

  it("a changed setting is never listed twice", () => {
    const m = setupRows([col(base), col(many)]);
    const modelRows = m.rows.filter((r) => r.name === "Model");
    expect(modelRows).toHaveLength(1);
    expect(modelRows[0]).toMatchObject({
      changed: true,
      values: ["claude-sonnet-4-5", "gpt-4o-mini"],
    });
    expect(m.rows.filter((r) => r.name === "Temperature")).toHaveLength(1);
    expect(m.rows.filter((r) => r.name === "Tools")).toHaveLength(1);
  });

  it("three lanes line up on the setting; a lane like the baseline is not bold", () => {
    const hot = run("#3C", {
      model: {
        ...config.model,
        params: { temperature: 0.9, max_tokens: 400 },
      },
    });
    const m = setupRows([col(base), col(maxTokens), col(hot)]);
    const temp = changed(m).find((r) => r.name === "Temperature");
    expect(temp.values).toEqual(["0.2", "0.2", "0.9"]);
    expect(temp.differs).toEqual([false, false, true]);
    const max = changed(m).find((r) => r.name === "Max tokens");
    expect(max.values).toEqual(["400", "800", "400"]);
    expect(m.count).toBe(2);
  });

  it("blind lanes show nothing; runs without settings are named, not compared", () => {
    const m = setupRows([col(base), { run: null, info: null }]);
    expect(m.count).toBe(0);
    m.rows.forEach((r) => expect(r.values[1]).toBeNull());
    const bare = setupRows([
      col(base),
      { run: { run_id: "x" }, info: [["Model", "gpt-4o"]] },
    ]);
    expect(bare.count).toBe(0);
    expect(bare.notCompared).toEqual([1]);
  });

  it("names a setting from its chip's own words", () => {
    expect(
      settingName({
        axis: "params",
        label: "max tokens 400 → 800",
        detail: { param: "max_tokens", from: "400", to: "800" },
      }),
    ).toBe("Max tokens");
    expect(
      settingName({
        axis: "dataset",
        label: "split all rows → hard",
        detail: { field: "split", from: null, to: "hard" },
      }),
    ).toBe("Split");
    expect(settingName({ axis: "tools", label: "+x", detail: {} })).toBe(
      "Tools",
    );
  });
});
