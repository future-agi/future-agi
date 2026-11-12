// W4-5: a 403 refusal with DRF's `permission_denied` code is shown in its own
// words by the app's shared error toast, never as "Something went wrong".
import { beforeEach, describe, expect, it, vi } from "vitest";

const snack = vi.hoisted(() => vi.fn());
vi.mock("notistack", () => ({ enqueueSnackbar: snack }));

import { getSafeActionErrorMessage } from "../errorUtils";
import { handleError } from "../queryErrorHandler";

const fallback = "Something went wrong";
const denialBody = {
  status: false,
  code: "permission_denied",
  error: "You need edit access to this dataset.",
  result:
    "You need edit access to this dataset. Ask Asha Rao, Ravi Menon or Meera Iyer.",
  missing_permission: "edit",
};

describe("getSafeActionErrorMessage for permission refusals", () => {
  it("shows a 403 permission_denied message", () => {
    expect(
      getSafeActionErrorMessage(
        { response: { status: 403, data: denialBody } },
        fallback,
      ),
    ).toBe("You need edit access to this dataset.");
  });

  it("still hides a 403 without the code", () => {
    expect(
      getSafeActionErrorMessage(
        { response: { status: 403, data: { result: "Forbidden by policy" } } },
        fallback,
      ),
    ).toBe(fallback);
  });

  it("still hides an internal-looking permission_denied message", () => {
    expect(
      getSafeActionErrorMessage(
        {
          response: {
            status: 403,
            data: {
              code: "permission_denied",
              detail: "Traceback (most recent call last): acl check",
            },
          },
        },
        fallback,
      ),
    ).toBe(fallback);
  });
});

describe("the shared error toast (QueryCache / MutationCache onError)", () => {
  beforeEach(() => snack.mockClear());

  it("toasts the refusal's own words for a dataset access denial", () => {
    // utils/axios.js rejects with the body plus statusCode.
    handleError({ ...denialBody, statusCode: 403 });
    expect(snack).toHaveBeenCalledWith(denialBody.result, { variant: "error" });
  });

  it("keeps the generic toast for other 403s", () => {
    handleError({ status: false, result: "Forbidden", statusCode: 403 });
    expect(snack).toHaveBeenCalledWith(fallback, { variant: "error" });
  });
});
