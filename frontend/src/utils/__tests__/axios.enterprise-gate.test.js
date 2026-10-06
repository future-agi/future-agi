import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const { enqueueSnackbar } = await import("notistack");
const { default: axiosInstance } = await import("../axios");
const { ENTERPRISE_GATE_EVENT } =
  await import("src/components/feature-gate/enterprise-gate");
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
