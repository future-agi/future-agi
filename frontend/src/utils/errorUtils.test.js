import { describe, expect, it } from "vitest";
import { getRequestErrorMessage } from "./errorUtils";

describe("getRequestErrorMessage", () => {
  it("hides backend unknown-field wording when requested", () => {
    expect(
      getRequestErrorMessage(
        { response: { data: { error: "status: Unknown field." } } },
        "We couldn't save the synthetic dataset. Please review the form and try again.",
        { sanitizeTechnicalFieldErrors: true },
      ),
    ).toBe(
      "We couldn't save the synthetic dataset. Please review the form and try again.",
    );
  });

  it("keeps user-facing server messages unchanged", () => {
    expect(
      getRequestErrorMessage(
        {
          response: {
            data: { error: "A dataset with this name already exists." },
          },
        },
        "Fallback",
        { sanitizeTechnicalFieldErrors: true },
      ),
    ).toBe("A dataset with this name already exists.");
  });
});
