import { describe, expect, it } from "vitest";

import { OPENAPI_CONTRACT } from "../openapi-contract.generated";
import { validateContractedResponse } from "../openapi-contract";

describe("annotation task member summaries", () => {
  it("binds task assignees to only id, name and email without changing User", () => {
    const definitions = OPENAPI_CONTRACT.definitions;
    expect(
      definitions.AnnotationTask.properties.assigned_users.items.$ref,
    ).toBe("#/definitions/AnnotationTaskAssignee");
    expect(
      Object.keys(definitions.AnnotationTaskAssignee.properties).sort(),
    ).toEqual(["email", "id", "name"]);
    expect(definitions.User.properties).toHaveProperty("goals");
    expect(definitions.User.properties).toHaveProperty("organization");
  });

  it("accepts the member summary on task list and retrieve", () => {
    const task = {
      id: "776276a5-b641-4b9b-8a6a-f31765c74ca4",
      task_name: "Synthetic task",
      assigned_users: [
        {
          id: "b155f036-c8e2-4a13-9a47-e932a1c82db3",
          name: "Synthetic member",
          email: "member@example.test",
        },
      ],
    };
    for (const [url, data] of [
      [
        "/model-hub/annotation-tasks/",
        { count: 1, next: null, previous: null, results: [task] },
      ],
      [`/model-hub/annotation-tasks/${task.id}/`, task],
    ]) {
      expect(
        validateContractedResponse({
          config: { url, method: "get" },
          status: 200,
          data,
        }),
      ).toMatchObject({ ok: true });
    }
  });
});
