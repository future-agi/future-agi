// Merged on `invited`: `invites` carries links only and omits an invitee who
// already has an account.
export function normalizeInvites(result, submittedEmails = []) {
  const links = new Map(
    (Array.isArray(result?.invites) ? result.invites : []).map((invite) => [
      (invite?.email ?? "").toLowerCase(),
      invite?.invite_link ?? "",
    ]),
  );

  const emails = Array.isArray(result?.invited)
    ? result.invited
    : submittedEmails;

  return emails.map((email) => ({
    email,
    inviteLink: links.get((email ?? "").toLowerCase()) ?? "",
  }));
}
