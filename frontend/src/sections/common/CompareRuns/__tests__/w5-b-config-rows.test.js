// W5 B6: config rows are exactly the configDiff chips, grouped; unchanged
// settings show the shared value; task compare's extras.
import { describe, expect, it } from "vitest";
import { configDiff } from "src/sections/common/Runs/configDiff";
import {
  CONFIG_GROUPS,
  configRows,
  differencesText,
  settingValue,
  setupExtras,
} from "../configRows";

const base = {
  label: "#2B",
  config: {
    kind: "prompt",
    model: { id: "claude-sonnet-4-5", params: { temperature: 0.2 } },
    prompt: {
      id: "p1",
      name: "Support Reply",
      version: "V4",
      messages: [{ role: "system", content: "Be brief." }],
    },
    tools: [{ name: "lookup_order" }],
    dataset: { version: "v5", rows: 238 },
    evals: [{ id: "correctness", name: "Correctness", version: "v1" }],
    trials: 1,
  },
};
const cand = {
  label: "#7B",
  config: {
    ...base.config,
    model: { id: "gpt-4o-mini", params: { temperature: 0.9 } },
    prompt: {
      id: "p1",
      name: "Support Reply",
      version: "V5",
      messages: [{ role: "system", content: "Be brief and kind." }],
    },
  },
};

describe("configRows", () => {
  it("differences only = exactly the configDiff chips", () => {
    const chips = configDiff(base, cand);
    const m = configRows(base, cand);
    expect(m.count).toBe(chips.length);
    const rows = m.groups.flatMap((g) => g.rows);
    // grouped for display, so compare as sets (same chips, none added)
    expect(rows.map((r) => r.text).sort()).toEqual(
      chips.map((c) => c.text).sort(),
    );
    expect(rows.every((r) => r.changed)).toBe(true);
    expect(new Set(rows.map((r) => r.axis))).toEqual(
      new Set(["model", "params", "prompt"]),
    );
    const params = rows.find((r) => r.axis === "params");
    expect(params.name).toBe("temperature");
    expect([params.a, params.b]).toEqual(["0.2", "0.9"]);
  });
  it("groups follow the display order", () => {
    const titles = configRows(base, cand).groups.map((g) => g.title);
    const order = CONFIG_GROUPS.map(([t]) => t);
    expect(titles).toEqual(order.filter((t) => titles.includes(t)));
  });
  it("all settings adds the unchanged ones with their value", () => {
    const m = configRows(base, cand, { differencesOnly: false });
    const rows = m.groups.flatMap((g) => g.rows);
    expect(m.count).toBe(configDiff(base, cand).length);
    const tools = rows.find((r) => r.axis === "tools");
    expect(tools).toMatchObject({
      changed: false,
      a: "lookup_order",
      b: "lookup_order",
    });
    expect(rows.find((r) => r.axis === "dataset").a).toBe("v5 · 238 rows");
    expect(rows.find((r) => r.axis === "harness")).toBeUndefined();
  });
  it("same settings → no differences", () => {
    expect(configRows(base, base).count).toBe(0);
    expect(differencesText(0)).toBe("No differences");
    expect(differencesText(1)).toBe("1 difference");
    expect(differencesText(7)).toBe("7 differences");
  });
  it("settingValue and setupExtras", () => {
    expect(settingValue(base.config, "evals")).toBe("Correctness v1");
    expect(setupExtras(cand)).toEqual({
      Temperature: "0.9",
      "Prompt version": "Support Reply V5",
      "Dataset version": "v5",
      Evaluations: "Correctness v1",
    });
    expect(setupExtras({})).toEqual({});
  });
});
