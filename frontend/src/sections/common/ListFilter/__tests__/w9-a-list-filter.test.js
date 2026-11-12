// W9 A2 (W9-SCOPE §5): the generic list filter model: operators come from
// the filter contract, conditions validate with sentences, and the URL key
// `f` round-trips.
import { describe, expect, it } from "vitest";
import { FILTER_TYPE_ALLOWED_OPS } from "src/api/contracts/filter-contract.generated";
import {
  CONTRACT_TYPE,
  chipText,
  conditionProblem,
  filtersProblem,
  newCondition,
  opsFor,
  opWord,
  parseFilters,
  removeAt,
  replaceAt,
  serializeFilters,
} from "../listFilterModel";
import {
  DATASET_FIELDS,
  unknownFieldSentence,
} from "src/sections/develop/organize/datasetFields";

const F = (id) => DATASET_FIELDS.find((f) => f.id === id);
const cond = (column_id, filter_op, filter_value, extra = {}) => ({
  column_id,
  filter_config: {
    filter_type: CONTRACT_TYPE[F(column_id)?.type] ?? "text",
    filter_op,
    filter_value,
    ...extra,
  },
});

describe("operators by type", () => {
  it("every offered operator is allowed by the filter contract", () => {
    DATASET_FIELDS.forEach((field) => {
      const allowed = FILTER_TYPE_ALLOWED_OPS[CONTRACT_TYPE[field.type]];
      expect(allowed, field.id).toBeTruthy();
      const ops = opsFor(field);
      expect(ops.length, field.id).toBeGreaterThan(0);
      ops.forEach((op) => expect(allowed, `${field.id} ${op}`).toContain(op));
    });
  });

  it("names folder and date operators in their own words", () => {
    expect(opsFor(F("folder"))).toEqual(["equals", "starts_with", "is_null"]);
    expect(opWord(F("folder"), "starts_with")).toBe("is under");
    expect(opWord(F("folder"), "is_null")).toBe("is empty");
    expect(opWord(F("updated"), "greater_than")).toBe("after");
    expect(opsFor(F("tag"))).toEqual([
      "in",
      "not_in",
      "is_null",
      "is_not_null",
    ]);
    expect(opsFor(F("starred"))).toEqual(["equals"]);
  });

  it("a new condition starts with the first operator and an empty value", () => {
    expect(newCondition(F("tag"))).toEqual(cond("tag", "in", []));
    expect(newCondition(F("last_pass"))).toEqual(
      cond("last_pass", "less_than", null),
    );
    expect(newCondition(F("starred"))).toEqual(cond("starred", "equals", true));
  });
});

describe("validation sentences", () => {
  it("names an unknown field with the field list", () => {
    expect(
      conditionProblem(cond("lastpass", "less_than", 0.8), DATASET_FIELDS, {
        unknownSentence: unknownFieldSentence,
      }),
    ).toBe(
      // Last pass and Δ vs baseline join once the list answers them
      // (datasetFields ANSWERED_DATASET_FIELDS).
      "Unknown field “lastpass”. Fields: name, kind, source, owner, tag, folder, agent, starred, rows, experiments, updated, created, baseline, best, metadata.<key>.",
    );
  });

  it("asks for a number for a numeric operator", () => {
    expect(
      conditionProblem(cond("last_pass", "less_than", "abc"), DATASET_FIELDS),
    ).toBe("“less than” needs a number for Last pass.");
    expect(
      conditionProblem(cond("last_pass", "less_than", 0.8), DATASET_FIELDS),
    ).toBeNull();
    expect(
      conditionProblem(cond("rows", "between", [10, null]), DATASET_FIELDS),
    ).toBe("“between” needs two numbers for Rows.");
  });

  it("refuses an operator the field does not offer", () => {
    expect(conditionProblem(cond("tag", "less_than", 3), DATASET_FIELDS)).toBe(
      "“less than” is not an operator for Tag.",
    );
  });

  it("needs at least one value for any-of and a date for dates", () => {
    expect(conditionProblem(cond("tag", "in", []), DATASET_FIELDS)).toBe(
      "“is any of” needs at least one value for Tag.",
    );
    expect(
      conditionProblem(cond("updated", "greater_than", "soon"), DATASET_FIELDS),
    ).toBe("“after” needs a date for Updated.");
    expect(
      conditionProblem(cond("folder", "is_null", null), DATASET_FIELDS),
    ).toBeNull();
    expect(
      conditionProblem(cond("folder", "starts_with", " "), DATASET_FIELDS),
    ).toBe("“is under” needs a value for Folder.");
  });

  it("reports the first problem of a list", () => {
    expect(
      filtersProblem(
        [cond("tag", "in", ["golden"]), cond("rows", "less_than", "x")],
        DATASET_FIELDS,
      ),
    ).toBe("“less than” needs a number for Rows.");
    expect(filtersProblem("nope", DATASET_FIELDS)).toBe(
      "Filters must be a list of conditions.",
    );
  });
});

describe("URL round trip and chips", () => {
  const filters = [
    cond("tag", "in", ["golden", "prod"]),
    cond("delta_vs_baseline", "less_than", 0, { significant: true }),
  ];

  it("serializes to compact JSON and parses back", () => {
    const text = serializeFilters(filters);
    expect(text).not.toContain(" ");
    expect(parseFilters(text)).toEqual({ filters, error: null });
    expect(serializeFilters([])).toBe("");
    expect(parseFilters("")).toEqual({ filters: [], error: null });
  });

  it("an unreadable link gives no filters and a reason", () => {
    expect(parseFilters("{bad")).toEqual({
      filters: [],
      error: "The filter in the link is not readable.",
    });
    expect(parseFilters('[{"column_id":"tag"}]').error).toBe(
      "Some conditions in the link were not readable and were left out.",
    );
  });

  it("chips read as sentences", () => {
    const labelOf = (field, v) => (field.id === "agent" ? `Agent ${v}` : null);
    expect(chipText(cond("tag", "in", ["golden"]), DATASET_FIELDS)).toBe(
      "Tag is golden",
    );
    expect(chipText(filters[0], DATASET_FIELDS)).toBe(
      "Tag is any of golden, prod",
    );
    expect(chipText(cond("last_pass", "less_than", 0.8), DATASET_FIELDS)).toBe(
      "Last pass < 80%",
    );
    expect(chipText(filters[1], DATASET_FIELDS)).toBe(
      "Δ vs baseline < 0 pts (significant)",
    );
    expect(
      chipText(cond("delta_vs_baseline", "greater_than", -3.1), DATASET_FIELDS),
    ).toBe("Δ vs baseline > −3.1 pts");
    expect(
      chipText(
        cond("folder", "starts_with", "support/billing"),
        DATASET_FIELDS,
      ),
    ).toBe("Folder is under support/billing");
    expect(chipText(cond("folder", "is_null", null), DATASET_FIELDS)).toBe(
      "Folder is empty",
    );
    expect(chipText(cond("starred", "equals", true), DATASET_FIELDS)).toBe(
      "Starred is true",
    );
    expect(chipText(cond("agent", "in", ["g1"]), DATASET_FIELDS, labelOf)).toBe(
      "Agent is Agent g1",
    );
  });

  it("removes and replaces one condition", () => {
    expect(removeAt(filters, 0)).toEqual([filters[1]]);
    const next = cond("tag", "in", ["golden"]);
    expect(replaceAt(filters, 0, next)).toEqual([next, filters[1]]);
  });
});
