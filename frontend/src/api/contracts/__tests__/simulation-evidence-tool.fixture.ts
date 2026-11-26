import type { SimulationEvidenceToolApi } from "../../../generated/api-contracts/api.schemas";

export const tool = {
  name: "lookup",
  arguments: [false, { ids: [1, 2] }],
  result: "truncated",
  ok: true,
} satisfies SimulationEvidenceToolApi;
