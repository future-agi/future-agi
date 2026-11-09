import { describe, expect, it } from "vitest";

import {
  ModelHubDatasetsClassifyColumnCreateBody,
  ModelHubDatasetsExtractEntitiesCreateBody,
  SimulateScenariosCreateCreateBody,
} from "src/generated/api-contracts/api.zod";

import { OPENAPI_CONTRACT } from "../openapi-contract.generated";

const cases = [
  {
    definition: "ScenarioCreateRequest",
    schema: SimulateScenariosCreateCreateBody,
    field: "model",
    data: { name: "Scenario" },
  },
  {
    definition: "ExtractEntitiesRequest",
    schema: ModelHubDatasetsExtractEntitiesCreateBody,
    field: "language_model_id",
    data: {
      column_id: "11111111-1111-4111-8111-111111111111",
      instruction: "Extract companies",
    },
  },
  {
    definition: "ClassifyColumnRequest",
    schema: ModelHubDatasetsClassifyColumnCreateBody,
    field: "language_model_id",
    data: {
      column_id: "11111111-1111-4111-8111-111111111111",
      labels: ["positive", "negative"],
    },
  },
];

describe.each(cases)(
  "$definition model default",
  ({ definition, schema, field, data }) => {
    it("defaults omitted models to gpt-4o in both generated contracts", () => {
      expect(schema.parse(data)[field]).toBe("gpt-4o");
      expect(
        OPENAPI_CONTRACT.definitions[definition].properties[field].default,
      ).toBe("gpt-4o");
    });

    it("preserves an explicitly selected model", () => {
      expect(schema.parse({ ...data, [field]: "custom-model" })[field]).toBe(
        "custom-model",
      );
    });
  },
);
