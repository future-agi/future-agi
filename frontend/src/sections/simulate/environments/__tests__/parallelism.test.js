import { describe, it, expect } from "vitest";
import { MAX_PARALLELISM, clampParallelism } from "../parallelism.constants";

// The build form offers "Parallel worlds" and sends only `runtime.parallelism`.
// HarnessRuntimeSerializer then rejects "voice parallelism must not exceed
// cpu_units" for livekit, vapi, retell, phone and auto — and a repo or upload
// source is always "auto". With no cpu_units in the payload the value that
// applies is the serializer default of 4 (Daytona declares no fixed_resources,
// and E2B's ALK_E2B_TEMPLATE_CPU_UNITS also defaults to 4). Offering more than
// that is offering a guaranteed 400.
describe("parallelism ceiling", () => {
  it("stops at the backend's cpu_units", () => {
    expect(MAX_PARALLELISM).toBe(4);
  });

  it("clamps an over-ceiling request rather than sending it", () => {
    expect(clampParallelism(8)).toBe(4);
    expect(clampParallelism(100)).toBe(4);
  });

  it("keeps a request inside the ceiling, and floors at one world", () => {
    expect(clampParallelism(4)).toBe(4);
    expect(clampParallelism(2)).toBe(2);
    expect(clampParallelism(0)).toBe(1);
    expect(clampParallelism("")).toBe(1);
    expect(clampParallelism(2.9)).toBe(2);
  });
});
