import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import { endpoints } from "src/utils/axios";
import { API_CONTRACT_EXCEPTIONS } from "src/api/contracts/api-contract-exceptions";
import {
  apiPath,
  getContractedApiMethods,
  isApiContractExceptionPath,
  isContractedApiPath,
} from "src/api/contracts/api-surface";

const LISTING = "/agentcc/decisions/models/";
const fromFrontend = (path) =>
  readFileSync(resolve(process.cwd(), path), "utf8");
const fromRepo = (path) =>
  readFileSync(resolve(process.cwd(), "..", path), "utf8");

// The evaluator picker lists the workspace's decision models from
// GET /agentcc/decisions/models/. The backend serves it and the generated
// contract lists it, so the picker reads it like any other documented path.
describe("the decision-model listing is a served, documented path", () => {
  it("reads the listing through the generated contract, not an exception", () => {
    expect(isContractedApiPath(LISTING)).toBe(true);
    expect(getContractedApiMethods(LISTING)).toEqual(["get"]);
    expect(isApiContractExceptionPath(LISTING)).toBe(false);
    expect(endpoints.develop.decisionModels).toBe(apiPath(LISTING));
  });

  // The endpoint registry check behind contracts:check fails when an
  // exception names a path the generated contract already lists.
  it("keeps no exception for a path the generated contract lists", () => {
    expect(
      Object.keys(API_CONTRACT_EXCEPTIONS).filter((path) =>
        isContractedApiPath(path),
      ),
    ).toEqual([]);
  });

  it("builds the listing with apiPath in the endpoint registry", () => {
    const registry = fromFrontend("src/utils/axios.js");
    expect(registry).toContain(`decisionModels: apiPath("${LISTING}")`);
    expect(registry).not.toContain(`uncontractedApiPath("${LISTING}")`);
  });

  it("is routed by the open-source backend, outside the enterprise apps", () => {
    const appUrls = fromRepo("futureagi/agentcc/urls.py");
    expect(appUrls).toContain(
      "from agentcc.views.decision_models import DecisionModelsView",
    );
    expect(appUrls).toMatch(
      /path\(\s*"decisions\/models\/",\s*DecisionModelsView\.as_view\(\),/,
    );
    // Mounted directly in the root URL list, not behind an installed-app
    // check such as the enterprise falcon-ai mount.
    expect(fromRepo("futureagi/tfc/urls.py")).toContain(
      '\n    path("agentcc/", include("agentcc.urls")),\n',
    );
    const view = fromRepo("futureagi/agentcc/views/decision_models.py");
    expect(view).toContain("class DecisionModelsView(APIView):");
    expect(view).toMatch(/^ {4}def get\(self, request\):$/m);
    expect(view).not.toMatch(/^\s*(?:from|import)\s+ee\b/m);
  });
});
