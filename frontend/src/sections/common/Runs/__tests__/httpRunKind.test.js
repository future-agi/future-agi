import { describe, expect, it } from "vitest";
import { canRerunHere, defaultRunsTab, kindLabel, runKindOf } from "../runKind";

// The run header returned by the canonical projection and Results catalog.
const HTTP_RUN = {
  run_id: "endpoint-run",
  task_kind: "external",
  source: "http",
  kind: "execution",
  config: {},
  native_config_id: "endpoint-source",
  output_column_id: "endpoint-output",
};

const PROMPT_RUN = {
  run_id: "prompt-run",
  task_kind: "prompt",
  source: "native",
  kind: "execution",
  config: {},
};

describe("canonical HTTP run kind", () => {
  it("labels a server-shaped HTTP header as an Endpoint", () => {
    expect(runKindOf(HTTP_RUN)).toBe("http");
    expect(kindLabel(runKindOf(HTTP_RUN))).toBe("Endpoint");
    expect(runKindOf({ ...HTTP_RUN, config: undefined })).toBe("http");
    expect(canRerunHere(HTTP_RUN)).toBe(true);
    expect(runKindOf({ task_kind: "external", target_type: "http" })).toBe(
      "http",
    );
  });

  it("opens mixed HTTP and prompt runs on the matrix", () => {
    expect(defaultRunsTab([HTTP_RUN, PROMPT_RUN])).toBe("matrix");
    expect(
      defaultRunsTab([
        HTTP_RUN,
        { ...HTTP_RUN, run_id: "another-endpoint-run" },
      ]),
    ).toBe("data");
  });

  it("preserves explicit kinds and recognizes caller execution", () => {
    expect(runKindOf({ ...HTTP_RUN, config: { kind: "agent" } })).toBe("agent");
    expect(runKindOf({ ...HTTP_RUN, source: { kind: "http" } })).toBe("http");
    expect(runKindOf(PROMPT_RUN)).toBe("prompt");
    expect(runKindOf({ ...HTTP_RUN, source: "external" })).toBe("upload");
  });
});
