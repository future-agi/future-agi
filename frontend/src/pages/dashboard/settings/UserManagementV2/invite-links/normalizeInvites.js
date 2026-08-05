// `invite_link` is per-entry optional: absent on Cloud/EE, and absent for an
// invitee who already has an account. The fallback keeps the UI saying
// "invited, no link" rather than rendering nothing.
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
