import { describe, expect, it } from "vitest";
import { getObservationFieldsQueryKey } from "../observationFieldsQueryKey";

describe("getObservationFieldsQueryKey", () => {
  it("uses the observe route project id before the prototype route id", () => {
    expect(
      getObservationFieldsQueryKey({
        observeId: "observe-project",
        projectId: "prototype-project",
      }),
    ).toEqual(["observationFields", "observe-project"]);
  });

  it("uses the prototype route project id when observeId is absent", () => {
    expect(
      getObservationFieldsQueryKey({ projectId: "prototype-project" }),
    ).toEqual(["observationFields", "prototype-project"]);
  });

  it("uses one stable cache key when the route has no project id", () => {
    expect(getObservationFieldsQueryKey()).toEqual(["observationFields", null]);
  });
});
