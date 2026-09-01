import { http, HttpResponse } from "msw";
import { HOST_API } from "src/config-global";

const TAKEN_EMAIL = "taken@futureagi.com";
const EXPIRED_TOKEN = "expired";

export const gcpMarketplaceSignup = http.post(
  `${HOST_API}/accounts/gcp-marketplace/signup/`,
  async ({ request }) => {
    const body = await request.json();

    if (!body.onboarding_token) {
      return HttpResponse.json(
        { status: false, error: "Missing onboarding token" },
        { status: 400 },
      );
    }

    if (body.onboarding_token === EXPIRED_TOKEN) {
      return HttpResponse.json(
        { status: false, error: "Invalid or expired onboarding token" },
        { status: 400 },
      );
    }

    if (body.email === TAKEN_EMAIL) {
      return HttpResponse.json(
        { status: false, error: "An account with this email already exists" },
        { status: 400 },
      );
    }

    return HttpResponse.json({
      status: true,
      result: {
        message: "Account created successfully",
        user_email: body.email,
        user_id: crypto.randomUUID(),
        access: `mock-access-${crypto.randomUUID()}`,
        refresh: `mock-refresh-${crypto.randomUUID()}`,
        new_org: true,
      },
    });
  },
);
