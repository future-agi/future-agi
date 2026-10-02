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

  it("adds retry guidance after sanitizing technical field errors", () => {
    expect(
      getRequestErrorMessage(
        {
          response: {
            status: 429,
            data: { error: "status: Unknown field." },
          },
        },
        "We couldn't regenerate the synthetic dataset. Please try again.",
        {
          retryAction: "regenerating this synthetic dataset",
          sanitizeTechnicalFieldErrors: true,
        },
      ),
    ).toBe(
      "We couldn't regenerate the synthetic dataset. Please try again. Please try regenerating this synthetic dataset again in a few minutes.",
    );
  });
});
