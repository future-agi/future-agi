import { describe, expect, it } from "vitest";
import { toStoredTraceId } from "./trace-id";

const DASHED = "4bf92f35-77b3-4da0-a3ce-929d0e0e4736";
const OPENTELEMETRY = "4bf92f3577b34da0a3ce929d0e0e4736";

describe("toStoredTraceId", () => {
  it("gives an OpenTelemetry trace id in the span store's dashed form", () => {
    expect(toStoredTraceId(OPENTELEMETRY)).toBe(DASHED);
  });

  it("gives a dashed trace id in lowercase", () => {
    expect(toStoredTraceId(DASHED)).toBe(DASHED);
    expect(toStoredTraceId(DASHED.toUpperCase())).toBe(DASHED);
    expect(toStoredTraceId(OPENTELEMETRY.toUpperCase())).toBe(DASHED);
    expect(toStoredTraceId(` ${OPENTELEMETRY} `)).toBe(DASHED);
  });

  it.each([
    "trace-1",
    OPENTELEMETRY.slice(0, -1),
    `${OPENTELEMETRY}0`,
    `${OPENTELEMETRY.slice(0, -1)}g`,
    DASHED.replaceAll("-", "_"),
    "",
  ])("gives %j as it is", (value) => {
    expect(toStoredTraceId(value)).toBe(value);
  });

  it("gives a missing id as it is", () => {
    expect(toStoredTraceId(null)).toBe(null);
    expect(toStoredTraceId(undefined)).toBe(undefined);
  });
});
