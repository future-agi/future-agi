import { describe, expect, it } from "vitest";
import { runScopeBody } from "../runScopeBody";
import { wireExperimentBody } from "../serverBody";
import { scopeReceipt } from "../scopeReceipt";

describe("wizard scope admission", () => {
  it("sends an error policy only when one was chosen", () => {
    expect(runScopeBody({ name: "Picked rows" })).not.toHaveProperty(
      "error_policy",
    );
    expect(runScopeBody({ error_policy: null })).not.toHaveProperty(
      "error_policy",
    );
    expect(runScopeBody({ error_policy: "zero" }).error_policy).toBe("zero");
  });

  it("moves identical per-card limits to the experiment policy without mutation", () => {
    const body = {
      prompt_config: [
        { name: "A", row_limits: { tokens: 500, messages: 2 } },
        { name: "B", row_limits: { messages: 2, tokens: 500 } },
      ],
    };
    const original = structuredClone(body);
    expect(runScopeBody(body)).toEqual({
      limits: { tokens: 500, messages: 2 },
      prompt_config: [{ name: "A" }, { name: "B" }],
    });
    expect(body).toEqual(original);
  });

  it.each([
    [{ row_limits: { tokens: 500 } }, { row_limits: { tokens: 600 } }],
    [{ row_limits: { tokens: 500 } }, {}],
  ])(
    "refuses mixed policies instead of silently limiting another run",
    (...entries) => {
      const wire = wireExperimentBody({ prompt_config: entries }, {}, false);
      expect(
        wire.blocks.some((b) => b.field === "prompt_config[].row_limits"),
      ).toBe(true);
    },
  );

  it("does not override an explicit conflicting experiment policy", () => {
    const wire = wireExperimentBody(
      {
        limits: { tokens: 200 },
        prompt_config: [{ row_limits: { tokens: 500 } }],
      },
      {},
      false,
    );
    expect(
      wire.blocks.some((b) => b.field === "prompt_config[].row_limits"),
    ).toBe(true);
  });
});

describe("persisted scope receipt", () => {
  it("uses only the server-selected count, sample, limits and policy", () => {
    expect(
      scopeReceipt({
        run_scope: {
          source_count: 8,
          eligible_count: 6,
          selected_count: 3,
          sample: { size: 3, seed: 17 },
        },
        limits: { tokens: 500 },
        error_policy: "exclude",
      }),
    ).toEqual({
      examples: 3,
      lines: [
        "Server selected 3 of 6 eligible rows (8 source rows)",
        "Sample size 3 · seed 17",
        "Limits per row · 500 tokens",
        "Errors excluded from scores",
      ],
    });
  });

  it("preserves zero selected rows and never invents a count from the draft", () => {
    expect(
      scopeReceipt({
        run_scope: { selected_count: 0, eligible_count: 0, source_count: 0 },
        error_policy: "zero",
      }).examples,
    ).toBe(0);
    expect(scopeReceipt({}).examples).toBeNull();
    expect(scopeReceipt({}).lines).toEqual([]);
  });
});
