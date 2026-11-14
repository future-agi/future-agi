import { describe, expect, it } from "vitest";
import { isUnsupportedTaskNativePath } from "../taskEvalMapping";

const span = {
  id: "native-span",
  project_id: "project",
  start_time: "2026-10-24",
  input: "hello",
  span_attributes: { locale: "en", "customer.id": "custom" },
};
const trace = {
  id: "native-trace",
  input: {},
  metadata: { id: "custom" },
  spans: [span],
};
const session = { id: "native-session", name: "chat", traces: [trace] };

describe("task mapping native-field contract", () => {
  it.each([
    ["spans", span, "id"],
    ["spans", span, "project_id"],
    ["spans", span, "start_time"],
    ["traces", trace, "id"],
    ["traces", trace, "spans.0.id"],
    ["sessions", session, "id"],
    ["sessions", session, "traces.0.id"],
    ["sessions", session, "traces.0.spans.0.id"],
    ["sessions", session, "traces.4.spans.7.id"],
  ])("rejects native-only %s path %s / %s", (type, row, path) => {
    expect(isUnsupportedTaskNativePath(path, type, row)).toBe(true);
  });

  it.each([
    ["spans", span, "input"],
    ["spans", span, "locale"],
    ["spans", span, "customer.id"],
    ["spans", span, "new.attribute.path"],
    ["traces", trace, "metadata.id"],
    ["traces", trace, "spans.0.locale"],
    ["traces", trace, "spans.0.span_attributes.customer.id"],
    ["traces", trace, "filter_spans.0.id"],
    ["traces", trace, "spans"],
    ["sessions", session, "traces"],
    ["sessions", session, "traces.0.spans.0.input"],
    ["voiceCalls", { started_at: "2026-10-24" }, "started_at"],
  ])("preserves supported/attribute %s path %s / %s", (type, row, path) => {
    expect(isUnsupportedTaskNativePath(path, type, row)).toBe(false);
  });

  it("preserves native-name collisions backed by actual attributes or the exact attribute inventory", () => {
    expect(
      isUnsupportedTaskNativePath("id", "spans", {
        ...span,
        span_attributes: { id: "custom" },
      }),
    ).toBe(false);
    expect(isUnsupportedTaskNativePath("id", "spans", span, ["id"])).toBe(
      false,
    );
    expect(
      isUnsupportedTaskNativePath("id", "spans", {
        ...span,
        span_attributes: { "id.value": "custom" },
      }),
    ).toBe(false);
    expect(
      isUnsupportedTaskNativePath("traces.0.spans.0.id", "sessions", {
        traces: [{ spans: [{ ...span, span_attributes: { id: "custom" } }] }],
      }),
    ).toBe(false);
  });
});
