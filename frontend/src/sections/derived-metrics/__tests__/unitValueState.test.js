import { describe, expect, it } from "vitest";
import { pointValueState, unitValueState } from "../unitValueState";
import { metricCopy, noValueReasons, snapshotStateCopy } from "../copy";

// Shape: MetricUnitValueSerializer + MetricFieldMetadataSerializer (B05).
const unit = (overrides = {}) => ({
  unit_id: "synthetic-unit",
  status: "value",
  no_value_reason: "",
  value_number: null,
  value_bool: null,
  value_text: null,
  inputs: {},
  inputs_bool: {},
  inputs_text: {},
  eligible_count: 1,
  evaluated_count: 1,
  missing_count: 0,
  influence: null,
  ...overrides,
});
const field = (value_type, overrides = {}) => ({
  id: "metric_field:synthetic",
  level: "conversation",
  kind: "custom",
  value_type,
  value_format: "",
  unit: "",
  value_labels: {},
  operators: [],
  projection: false,
  ...overrides,
});

describe("unit value states", () => {
  it.each([null, undefined])("keeps a missing record (%s) missing", (value) => {
    expect(unitValueState(value, field("number"))).toMatchObject({
      state: "no_value",
      value: null,
    });
  });
  it.each(["number", "boolean", "text"])(
    "does not coerce a nullable %s",
    (type) => {
      expect(unitValueState(unit(), field(type))).toMatchObject({
        state: "no_value",
        value: null,
      });
    },
  );
  it.each([
    ["number", "value_number", 0],
    ["number", "value_number", -2.5],
    ["boolean", "value_bool", false],
    ["boolean", "value_bool", true],
    ["text", "value_text", ""],
    ["text", "value_text", "false"],
  ])("preserves a real %s value %s=%s", (type, key, value) => {
    expect(unitValueState(unit({ [key]: value }), field(type))).toMatchObject({
      state: "value",
      value,
      valueType: type,
    });
  });
  it.each([NaN, Infinity, -Infinity])(
    "rejects non-finite number %s",
    (value_number) => {
      expect(
        unitValueState(unit({ value_number }), field("number")),
      ).toMatchObject({
        state: "no_value",
        label: "No value: not a finite number",
        value: null,
      });
    },
  );
  it.each([
    ["number", "value_number", "0"],
    ["number", "value_number", false],
    ["boolean", "value_bool", 0],
    ["boolean", "value_bool", "false"],
    ["text", "value_text", 0],
    ["text", "value_text", {}],
  ])("rejects mismatched %s input", (type, key, value) => {
    expect(unitValueState(unit({ [key]: value }), field(type)).state).toBe(
      "unsupported",
    );
  });
  it.each(Object.entries(noValueReasons))(
    "renders reason %s without leaking a stale value",
    (reason, text) => {
      expect(
        unitValueState(
          unit({
            status: "no_value",
            no_value_reason: reason,
            value_number: 123,
          }),
          field("number"),
        ),
      ).toMatchObject({
        state: "no_value",
        label: `No value: ${text}`,
        value: null,
      });
    },
  );
  it.each(["", "new_reason", "__proto__", "<script>secret</script>"])(
    "handles an unknown reason %s safely",
    (no_value_reason) => {
      expect(
        unitValueState(
          unit({ status: "no_value", no_value_reason }),
          field("boolean"),
        ).label,
      ).toBe("No value");
    },
  );
  it.each([
    ["in_progress", "In progress"],
    ["not_calculated", "Not calculated yet"],
  ])("keeps %s distinct from false", (status, label) => {
    expect(
      unitValueState(unit({ status, value_bool: false }), field("boolean")),
    ).toMatchObject({ state: "pending", label, value: null });
  });
  it.each(["new_status", "", undefined])(
    "fails closed on status %s",
    (status) => {
      expect(
        unitValueState(unit({ status, value_number: 0 }), field("number"))
          .state,
      ).toBe("unsupported");
    },
  );
  it("does not accept the legacy hydrated-list envelope as a unit response", () => {
    expect(
      unitValueState(
        { state: "value", value: 1, format: "percent" },
        field("number"),
      ).state,
    ).toBe("unsupported");
  });
  it("does not guess the field type from populated value slots", () => {
    expect(unitValueState(unit({ value_number: 0 }), undefined).state).toBe(
      "unsupported",
    );
  });
  it.each([0, 0.25, 4])(
    "uses the numeric user projection %s, not the Boolean slot",
    (value_number) => {
      expect(
        unitValueState(
          unit({ value_number, value_bool: true }),
          field("number", { projection: true }),
        ),
      ).toMatchObject({ value: value_number, valueType: "number" });
    },
  );
  it("rejects a contradictory Boolean projection contract", () => {
    expect(
      unitValueState(
        unit({ value_bool: true }),
        field("boolean", { projection: true }),
      ).state,
    ).toBe("unsupported");
  });
  it("never derives a numeric projection from a Boolean alone", () => {
    expect(
      unitValueState(
        unit({ value_bool: false }),
        field("number", { projection: true }),
      ).state,
    ).toBe("no_value");
  });
});

describe("point states (MetricPointSerializer)", () => {
  it.each([0, 0.25, 100])("preserves numeric point %s", (value) => {
    expect(pointValueState({ point_status: "ok", value })).toMatchObject({
      state: "value",
      value,
    });
  });
  it.each([null, undefined])("keeps null point/value %s missing", (value) => {
    expect(pointValueState(value).state).toBe("no_value");
    expect(pointValueState({ point_status: "ok", value }).state).toBe(
      "no_value",
    );
  });
  it.each([false, true, "0"])(
    "does not coerce a non-numeric point %s",
    (value) => {
      expect(pointValueState({ point_status: "ok", value }).state).toBe(
        "unsupported",
      );
    },
  );
  it.each([
    "missing_inputs",
    "invalid_probability",
    "zero_denominator",
    "non_finite",
    "retention_expired",
  ])("renders point reason %s", (point_status) => {
    expect(pointValueState({ point_status, value: 0 })).toMatchObject({
      state: "no_value",
      label: `No value: ${noValueReasons[point_status]}`,
      value: null,
    });
  });
  it("distinguishes an empty window from a missing input", () => {
    expect(
      pointValueState({ point_status: "no_data", value: 0 }),
    ).toMatchObject({ state: "empty", label: "No data", value: null });
  });
  it.each(["unavailable", "future_status"])(
    "keeps %s explicit",
    (point_status) => {
      expect(pointValueState({ point_status, value: 0 }).state).toBe(
        "unsupported",
      );
    },
  );
  it("uses the exact sampled users explanation", () => {
    expect(
      pointValueState({
        point_status: "sampled_inputs_not_supported",
        value: null,
      }).label,
    ).toBe(
      "Estimate not available: user metrics need every conversation evaluated.",
    );
  });
});

describe("caller-owned read lifecycle", () => {
  it.each([unitValueState, pointValueState])(
    "never returns cached values on initial error or loading",
    (resolve) => {
      const value = {
        ...unit({ value_number: 42 }),
        point_status: "ok",
        value: 42,
        value_mode: "materialized",
      };
      const call = (options) =>
        resolve === unitValueState
          ? resolve(value, field("number"), options)
          : resolve(value, options);
      expect(call({ error: true })).toMatchObject({
        state: "error",
        label: metricCopy.error,
        value: null,
      });
      expect(call({ loading: true })).toMatchObject({
        state: "pending",
        label: metricCopy.loading,
        value: null,
      });
      expect(call({ loading: true, error: true }).state).toBe("error");
      expect(call({ stale: true })).toMatchObject({
        state: "stale",
        value: 42,
      });
    },
  );
  it("uses on-demand loading before and after metadata arrives", () => {
    expect(
      pointValueState(null, { loading: true, valueMode: "on_demand" }).label,
    ).toBe(metricCopy.onDemand);
    expect(
      pointValueState({ value_mode: "on_demand" }, { loading: true }).label,
    ).toBe(metricCopy.onDemand);
  });
  it("does not upgrade a missing value to stale", () => {
    expect(unitValueState(unit(), field("number"), { stale: true }).state).toBe(
      "no_value",
    );
  });
});

describe("snapshot copy", () => {
  it.each([
    ["saved", "Snapshot saved"],
    ["deleted", "Snapshot deleted"],
    ["future", "Snapshot unavailable"],
    [
      "expired",
      "Snapshot not saved: the data changed before it could be copied. Try again.",
    ],
  ])("labels %s", (state, label) => {
    expect(snapshotStateCopy(state)).toBe(label);
  });
  it("uses returned erasure count including zero, without invented measurements", () => {
    expect(
      snapshotStateCopy("changed", {
        date: "5 Nov",
        removedCount: 0,
        locale: "en-US",
      }),
    ).toBe(
      "Changed 5 Nov: 0 conversations removed after a data erasure request",
    );
    expect(snapshotStateCopy("changed")).toBe(
      "Snapshot changed after a data erasure request",
    );
  });
});
