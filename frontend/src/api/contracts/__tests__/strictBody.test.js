// strictBody reads what dev's strict serializers accept from the generated
// contract (the capability contract): no field list is typed here or in the module.
import { describe, expect, it } from "vitest";
import { OPENAPI_CONTRACT } from "../openapi-contract.generated";
import { STRICT_ROUTES } from "../strictRoutes.generated";
import {
  DSX_STRICT_ROUTES,
  acceptedFields,
  bodyDefinitionOf,
  droppedNestedFields,
  strictRouteOf,
  strictViolations,
  unknownFields,
  wireKeysOf,
} from "../strictBody";

describe("DSX_STRICT_ROUTES", () => {
  it("is the backend-derived list: contracted routes, most with a body definition", () => {
    // strictRoutes.generated.js comes from dev's URL resolver (the parity
    // test writes and checks it), not from a hand list.
    expect(DSX_STRICT_ROUTES.length).toBe(STRICT_ROUTES.length);
    expect(DSX_STRICT_ROUTES.length).toBeGreaterThan(100);
    let defined = 0;
    DSX_STRICT_ROUTES.forEach((r) => {
      expect(
        OPENAPI_CONTRACT.endpoints[r.path]?.[r.method],
        `${r.method} ${r.path}`,
      ).toBeTruthy();
      const def = bodyDefinitionOf(r.method, r.path);
      if (def) {
        expect(OPENAPI_CONTRACT.definitions[def]).toBeTruthy();
        defined += 1;
      }
    });
    expect(defined / DSX_STRICT_ROUTES.length).toBeGreaterThan(0.8);
    // The routes the verifier found missing from the old hand list.
    [
      ["post", "/model-hub/datasets/{dataset_id}/preview/{operation_type}/"],
      ["post", "/model-hub/develops/{dataset_id}/extract-json-column/"],
      ["post", "/model-hub/datasets/{dataset_id}/add-api-column/"],
    ].forEach(([method, path]) =>
      expect(DSX_STRICT_ROUTES).toContainEqual({ method, path }),
    );
  });

  it("matches a concrete URL to its strict route like the interceptor", () => {
    expect(
      strictRouteOf(
        "PUT",
        "/model-hub/experiments/v2/0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a/",
      ),
    ).toEqual({
      method: "put",
      path: "/model-hub/experiments/v2/{experiment_id}/",
      definition: "ExperimentUpdateV2",
    });
    expect(strictRouteOf("get", "/model-hub/experiments/v2/list/")).toBeNull();
  });
});

describe("acceptedFields / unknownFields", () => {
  it("reads the accepted keys from the contract, not from a hand list", () => {
    const fromContract = Object.keys(
      OPENAPI_CONTRACT.definitions.ExperimentCreateV2.properties,
    );
    expect(acceptedFields("ExperimentCreateV2")).toEqual(fromContract);
  });

  it("flags every top-level key dev answers 400 for, skipping undefined", () => {
    const body = {
      name: "x",
      dataset_id: "d",
      trials: 2,
      description: "",
      left_out: undefined,
    };
    expect(unknownFields(body, "ExperimentCreateV2")).toEqual([
      "description",
      "trials",
    ]);
  });

  it("reads the keys of a multipart body", () => {
    const fd = new FormData();
    fd.append("file", new Blob(["a"]), "a.csv");
    fd.append("new_dataset_name", "n");
    fd.append("column_roles", "{}");
    expect(wireKeysOf(fd).sort()).toEqual([
      "column_roles",
      "file",
      "new_dataset_name",
    ]);
    expect(unknownFields(fd, "CreateDatasetFromLocalFileRequest")).toEqual([
      "column_roles",
    ]);
  });

  it("strictViolations is empty for a route dev does not validate strictly", () => {
    expect(
      strictViolations("post", "/model-hub/experiments/v2/", {
        name: "n",
        scope: "all",
      }),
    ).toEqual(["scope"]);
    expect(
      strictViolations("get", "/model-hub/experiments/v2/list/", { a: 1 }),
    ).toEqual([]);
  });
});

describe("droppedNestedFields", () => {
  it("finds keys a nested serializer drops, never passthrough or free JSON", () => {
    const body = {
      prompt_config: [
        {
          prompt_id: "p",
          target_type: "prompt",
          variable_mapping: { q: "c" },
          // PromptModelParams keeps provider keys (additionalProperties).
          model_params: { temperature: 1, providers: "openai" },
          // MessageItem keeps extra keys too.
          messages: [{ role: "user", content: "x", extra: 1 }],
        },
      ],
      user_eval_metrics: [
        // config is a free JSON object on dev.
        { template_id: "t", name: "n", config: { any: 1 }, pinned: "v2" },
      ],
    };
    const found = droppedNestedFields(body, "ExperimentCreateV2").map((f) =>
      f.path.join("."),
    );
    expect(found).toEqual([
      "prompt_config.0.target_type",
      "prompt_config.0.variable_mapping",
      "user_eval_metrics.0.pinned",
    ]);
  });
});
