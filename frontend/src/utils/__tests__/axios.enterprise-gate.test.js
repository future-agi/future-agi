import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

vi.mock("../Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const { enqueueSnackbar } = await import("notistack");
const { default: axiosInstance } = await import("../axios");
const { ENTERPRISE_GATE_EVENT } = await import(
  "src/components/feature-gate/enterprise-gate"
);
const { handleError } = await import("../queryErrorHandler");

const rejected = axiosInstance.interceptors.response.handlers.find(
  (handler) => handler.rejected,
)?.rejected;

const memberGate = {
  feature: "members",
  edition: "community",
  limit: 3,
  current: 3,
  requested: 1,
  license_state: "missing",
  contact: "sales@futureagi.com",
  activation_route: "/dashboard/settings/ee-licenses",
};

function paymentRequired(data) {
  // An uncontracted URL keeps response contract validation out of the way.
  return {
    response: { status: 402, data },
    config: { url: "/not-contracted/edition-gate-test/", method: "post" },
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("402 Enterprise gate routing (TH-8084 AC-08)", () => {
  it("dispatches the gate event instead of an error snackbar", async () => {
    const seen = [];
    const listener = (event) => seen.push(event.detail);
    window.addEventListener(ENTERPRISE_GATE_EVENT, listener);
    try {
      await expect(
        rejected(
          paymentRequired({
            status: false,
            code: "ENTERPRISE_FEATURE_REQUIRED",
            message: "Community includes up to 3 organization members.",
            upgrade_required: true,
            error: { code: "ENTERPRISE_FEATURE_REQUIRED" },
            enterprise_gate: memberGate,
          }),
        ),
      ).rejects.toMatchObject({
        statusCode: 402,
        code: "ENTERPRISE_FEATURE_REQUIRED",
      });
    } finally {
      window.removeEventListener(ENTERPRISE_GATE_EVENT, listener);
    }
    expect(seen).toEqual([memberGate]);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("keeps the existing snackbar for product gates", async () => {
    await expect(
      rejected(
        paymentRequired({
          code: "LICENSE_MISSING",
          upgrade_required: true,
          error: {
            code: "LICENSE_MISSING",
            message: "'falcon_ai' is not available.",
          },
          enterprise_gate: { ...memberGate, feature: "falcon_ai", limit: null },
        }),
      ),
    ).rejects.toMatchObject({ statusCode: 402 });
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "'falcon_ai' is not available.",
      {
        variant: "error",
      },
    );
  });

  it("the global react-query handler does not toast an edition gate", () => {
    handleError({
      statusCode: 402,
      code: "ENTERPRISE_FEATURE_REQUIRED",
      result: "Community includes up to 3 organization members.",
      enterprise_gate: memberGate,
    });
    expect(enqueueSnackbar).not.toHaveBeenCalled();

    handleError({ statusCode: 400, result: "Something else" });
    expect(enqueueSnackbar).toHaveBeenCalled();
  });
});

// R5: the same refusal through a contracted endpoint with strict response
// validation on. The body is the real fourth-member 402 pinned by
// futureagi/accounts/tests/test_enterprise_gate_contract.py.
const GATE_MESSAGE =
  "Community includes up to 3 organization members. More members are an " +
  "Enterprise feature. Contact sales@futureagi.com or activate a license in " +
  "Settings > Plan & License.";
const fourthMember402 = {
  status: false,
  type: "entitlement_error",
  code: "ENTERPRISE_FEATURE_REQUIRED",
  detail: GATE_MESSAGE,
  message: GATE_MESSAGE,
  error: {
    code: "ENTERPRISE_FEATURE_REQUIRED",
    message: GATE_MESSAGE,
    detail: { feature: "members" },
  },
  result: GATE_MESSAGE,
  details: { feature: ["members"] },
  upgrade_required: true,
  enterprise_gate: memberGate,
};

function contracted(status, data, url = "/accounts/organization/invite/") {
  return { response: { status, data, config: { url, method: "post" } } };
}

describe("402 Enterprise gate under strict response contracts (R5)", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    "/accounts/organization/invite/",
    "/accounts/team/users/",
    "/accounts/workspaces/",
    "/accounts/organizations/new/",
  ])("validates and dispatches the gate for %s", async (url) => {
    const seen = [];
    const listener = (event) => seen.push(event.detail);
    window.addEventListener(ENTERPRISE_GATE_EVENT, listener);
    try {
      await expect(
        rejected(contracted(402, fourthMember402, url)),
      ).rejects.toMatchObject({
        statusCode: 402,
        code: "ENTERPRISE_FEATURE_REQUIRED",
      });
    } finally {
      window.removeEventListener(ENTERPRISE_GATE_EVENT, listener);
    }
    expect(seen).toEqual([memberGate]);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("rejects a gate whose typed fields are wrong", async () => {
    await expect(
      rejected(
        contracted(402, {
          ...fourthMember402,
          enterprise_gate: { ...memberGate, limit: "three" },
        }),
      ),
    ).rejects.toMatchObject({ name: "ApiContractValidationError" });
  });

  it("rejects a structured error missing required typed fields", async () => {
    await expect(
      rejected(
        contracted(402, {
          ...fourthMember402,
          error: { code: "ENTERPRISE_FEATURE_REQUIRED" },
        }),
      ),
    ).rejects.toMatchObject({ name: "ApiContractValidationError" });
  });

  it("keeps the checked-in generated clients typed for the string/object union", () => {
    const schemas = fs.readFileSync(
      path.resolve(process.cwd(), "src/generated/api-contracts/api.schemas.ts"),
      "utf8",
    );
    const zod = fs.readFileSync(
      path.resolve(process.cwd(), "src/generated/api-contracts/api.zod.ts"),
      "utf8",
    );
    expect(schemas).toMatch(
      /export type EnterpriseGateErrorResponseApiError =\s*\n\s*\| string\s*\n\s*\| \{/,
    );
    expect(zod).toContain(
      "export const EnterpriseGateErrorResponseApiError = zod.union",
    );
  });

  it("keeps ordinary string errors valid on the same endpoint", async () => {
    await expect(
      rejected(
        contracted(400, {
          status: false,
          type: "validation_error",
          code: "invalid",
          detail: "emails: This list may not be empty.",
          message: "emails: This list may not be empty.",
          error: "emails: This list may not be empty.",
          result: "emails: This list may not be empty.",
          details: { emails: ["This list may not be empty."] },
        }),
      ),
    ).rejects.not.toMatchObject({ name: "ApiContractValidationError" });
  });
});
