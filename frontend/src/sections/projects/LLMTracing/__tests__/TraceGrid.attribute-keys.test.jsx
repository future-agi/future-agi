import { describe, expect, it } from "vitest";

import {
  getRequestedTraceAttributeKeys,
  getTraceAttributeRequestKey,
  getTraceAttributeRequestParams,
} from "../traceAttributeRequest";

describe("TraceGrid exact custom attribute requests", () => {
  const standard = { id: "trace_name", groupBy: "System" };
  const finalStatus = {
    id: "final_status",
    groupBy: "Custom Columns",
    isVisible: true,
  };

  it("requests every selected custom key exactly once", () => {
    const commaKey = {
      id: "metadata.path,with-comma",
      groupBy: "Custom Columns",
    };

    expect(
      getRequestedTraceAttributeKeys([
        standard,
        finalStatus,
        commaKey,
        finalStatus,
      ]),
    ).toEqual(["final_status", "metadata.path,with-comma"]);
    expect(
      getTraceAttributeRequestParams([standard, finalStatus, commaKey]),
    ).toEqual({
      attribute_keys: JSON.stringify([
        "final_status",
        "metadata.path,with-comma",
      ]),
    });
  });

  it("omits cross-span hydration when no custom column is requested", () => {
    expect(getTraceAttributeRequestParams([standard])).toEqual({});
  });

  it("invalidates the datasource on add, remove, and visibility changes", () => {
    const withoutCustom = getTraceAttributeRequestKey([standard]);
    const withCustom = getTraceAttributeRequestKey([standard, finalStatus]);
    const hiddenCustom = getTraceAttributeRequestKey([
      standard,
      { ...finalStatus, isVisible: false },
    ]);

    expect(withCustom).not.toBe(withoutCustom);
    expect(hiddenCustom).toBe(withoutCustom);
    expect(hiddenCustom).not.toBe(withCustom);
    expect(getTraceAttributeRequestKey([standard])).toBe(withoutCustom);
  });
});
