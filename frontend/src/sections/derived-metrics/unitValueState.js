import { metricCopy, noValueCopy } from "./copy";

function absent(state, label) {
  return { state, label, value: null, valueType: null };
}

function readState({ loading, error, valueMode }) {
  // Do not leave an old value looking current during an error or a new request.
  if (error) return absent("error", metricCopy.error);
  if (loading) {
    return absent(
      "pending",
      valueMode === "on_demand" ? metricCopy.onDemand : metricCopy.loading,
    );
  }
  return null;
}

function present(value, valueType, stale) {
  if (value == null) return absent("no_value", metricCopy.noValue);
  if (
    valueType === "number" &&
    typeof value === "number" &&
    !Number.isFinite(value)
  ) {
    return absent("no_value", noValueCopy("non_finite"));
  }
  if (
    !["number", "boolean", "text"].includes(valueType) ||
    typeof value !== (valueType === "text" ? "string" : valueType)
  ) {
    return absent("unsupported", metricCopy.unsupported);
  }
  return { state: stale ? "stale" : "value", label: "", value, valueType };
}

/**
 * MetricUnitValueSerializer + MetricFieldMetadataSerializer, without aliases.
 * Read lifecycle is caller-owned UI state; it is not a fabricated server status.
 * This intentionally does not accept the older hydrated list-cell envelope.
 */
export function unitValueState(value, field, options = {}) {
  const lifecycle = readState(options);
  if (lifecycle) return lifecycle;
  if (value == null) return absent("no_value", metricCopy.noValue);
  switch (value.status) {
    case "in_progress":
      return absent("pending", metricCopy.inProgress);
    case "not_calculated":
      return absent("pending", metricCopy.notCalculated);
    case "no_value":
      return absent("no_value", noValueCopy(value.no_value_reason));
    case "value": {
      // Users projections are always numeric; never coerce a conversation bool.
      if (field?.projection === true && field.value_type !== "number") {
        return absent("unsupported", metricCopy.unsupported);
      }
      const key = {
        number: "value_number",
        boolean: "value_bool",
        text: "value_text",
      }[field?.value_type];
      if (!key) return absent("unsupported", metricCopy.unsupported);
      return present(value[key], field.value_type, options.stale);
    }
    default:
      return absent("unsupported", metricCopy.unsupported);
  }
}

/** MetricPointSerializer, selected explicitly rather than guessed by field names. */
export function pointValueState(point, options = {}) {
  const lifecycle = readState({
    ...options,
    valueMode: point == null ? options.valueMode : point.value_mode,
  });
  if (lifecycle) return lifecycle;
  if (point == null) return absent("no_value", metricCopy.noValue);
  switch (point.point_status) {
    case "ok":
      return present(point.value, "number", options.stale);
    case "no_data":
      return absent("empty", metricCopy.empty);
    case "not_calculated":
      return absent("pending", metricCopy.notCalculated);
    case "settling":
      return absent("pending", metricCopy.settling);
    case "sampled_inputs_not_supported":
      return absent("unsupported", metricCopy.sampledInputs);
    case "missing_inputs":
    case "invalid_probability":
    case "zero_denominator":
    case "non_finite":
    case "retention_expired":
      return absent("no_value", noValueCopy(point.point_status));
    default:
      return absent("unsupported", metricCopy.unsupported);
  }
}
