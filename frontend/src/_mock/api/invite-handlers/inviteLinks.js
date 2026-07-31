import { http, HttpResponse } from "msw";
import { HOST_API } from "src/config-global";

// TH-7217 CLEANUP: delete with its handlers.js entries once the backend
// returns `invites: [{ email, invite_link }]`.
//
// The link shape mirrors what the invite email already builds
// (accounts/templates/invite_user.html):
//   {APP_URL}/auth/jwt/invitation/accept/{uid}/{token}
// so the copied link and the emailed link stay the same journey.

const fakeUid = (email) =>
  btoa(email).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");

const fakeToken = (email) => {
  let hash = 0;
  for (let i = 0; i < email.length; i += 1) {
    hash = (hash * 31 + email.charCodeAt(i)) % 0xffffffff;
  }
  return `mock-${hash.toString(36)}-${email.length.toString(36)}`;
};

export const createInviteLinks = http.post(
  `${HOST_API}/accounts/organization/invite/`,
  async ({ request }) => {
    const body = await request.json().catch(() => ({}));
    const emails = Array.isArray(body?.emails) ? body.emails : [];

    await new Promise((resolve) => setTimeout(resolve, 350));

    const invites = emails.map((email) => ({
      email,
      invite_link: `${window.location.origin}/auth/jwt/invitation/accept/${fakeUid(email)}/${fakeToken(email)}`,
    }));

    return HttpResponse.json({
      status: true,
      result: {
        invited: emails,
        invites,
        errors: [],
      },
    });
  },
);

export const cancelInviteLink = http.delete(
  `${HOST_API}/accounts/organization/invite/cancel/`,
  async () => {
    await new Promise((resolve) => setTimeout(resolve, 200));
    return HttpResponse.json({ status: true, result: { cancelled: true } });
  },
);
