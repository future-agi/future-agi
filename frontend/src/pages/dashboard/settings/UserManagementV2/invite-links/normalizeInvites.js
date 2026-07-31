// Reads the invite links off a create-invite response.
//
// The backend builds this link already — it is the same one the invite email
// carries (`accounts/templates/invite_user.html`:
// {APP_URL}/auth/jwt/invitation/accept/{uid}/{token}) — but does not yet return
// it. Contract addition, additive so the existing `invited` consumer is
// untouched:
//   result: { invited: [email], invites: [{ email, invite_link }], errors: [] }
//
// Until that ships, fall back to the emails we submitted with empty links, so
// the UI says "invited, no link" rather than rendering nothing.
export function normalizeInvites(result, submittedEmails = []) {
  if (Array.isArray(result?.invites) && result.invites.length) {
    return result.invites.map((invite) => ({
      email: invite?.email ?? "",
      inviteLink: invite?.invite_link ?? "",
    }));
  }

  const emails = Array.isArray(result?.invited)
    ? result.invited
    : submittedEmails;
  return emails.map((email) => ({ email, inviteLink: "" }));
}
