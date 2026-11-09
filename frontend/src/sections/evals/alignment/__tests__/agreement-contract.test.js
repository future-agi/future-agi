import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getContractedApiMethods,
  isApiContractExceptionPath,
} from "src/api/contracts/api-surface";
import { surfaceShownIn } from "src/launch/launchSwitch";

const AGREEMENT = "/model-hub/eval-templates/{template_id}/agreement/";

afterEach(() => vi.resetModules());

describe("stored agreement API contract", () => {
  it("uses the generated GET contract, not a prototype exception", () => {
    expect(getContractedApiMethods(AGREEMENT)).toEqual(["get"]);
    expect(isApiContractExceptionPath(AGREEMENT)).toBe(false);
  });

  it("builds the public endpoint through the contracted helper", async () => {
    const apiPath = vi.fn(() => "/contracted-agreement/");
    const { endpoints } = await vi.importActual("src/utils/axios");
    // First check the real registry preserves path-parameter encoding.
    expect(endpoints.develop.w7.agreement("template/one")).toBe(
      "/model-hub/eval-templates/template%2Fone/agreement/",
    );
    vi.resetModules();
    vi.doMock("src/api/contracts/api-surface", async (importOriginal) => ({
      ...(await importOriginal()),
      apiPath,
    }));
    try {
      const { endpoints: registry } = await import("src/utils/axios");
      expect(registry.develop.w7.agreement("template/one")).toBe(
        "/contracted-agreement/",
      );
      expect(apiPath).toHaveBeenCalledWith(AGREEMENT, {
        template_id: "template/one",
      });
    } finally {
      vi.doUnmock("src/api/contracts/api-surface");
    }
  });

  it("keeps agreement default-off and never reveals judge tools with its claim", () => {
    const hidden = {
      launch: true,
      experimentsBasic: false,
      madeReal: new Set(),
    };
    expect(surfaceShownIn("evals.agreement", hidden)).toBe(false);
    const candidate = { ...hidden, madeReal: new Set(["evals.agreement"]) };
    expect(surfaceShownIn("evals.agreement", candidate)).toBe(true);
    expect(surfaceShownIn("evals.agreementTools", candidate)).toBe(false);
  });
});
