import { describe, expect, it } from "vitest";
import { CODE_EXECUTION_PORTS } from "../constants";

describe("agent playground code execution constants", () => {
  it("keeps the result port aligned with the runner metadata envelope", () => {
    const resultPort = CODE_EXECUTION_PORTS.find((port) => port.key === "result");

    expect(resultPort.data_schema.required).toContain("metadata");
    expect(resultPort.data_schema.properties.metadata.required).toEqual([
      "language",
      "runner",
      "timed_out",
      "memory_mb",
    ]);
  });
});
