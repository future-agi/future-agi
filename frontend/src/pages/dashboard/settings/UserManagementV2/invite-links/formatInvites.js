// Tab separated so it pastes into a spreadsheet as two columns and into a chat
// as one line per teammate.
const COLUMNS = ["Email", "Invite link"];

export function formatInvitesForCopy(invites = []) {
  const withLinks = invites.filter((invite) => invite.inviteLink);
  if (!withLinks.length) return "";

  return [COLUMNS, ...withLinks.map((i) => [i.email, i.inviteLink])]
    .map((cells) => cells.join("\t"))
    .join("\n");
}
