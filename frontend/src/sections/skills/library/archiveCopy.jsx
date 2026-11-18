import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { labelDisplayName } from "../utils/terms";

// What the Archive confirm tells the person before the one-way step (UX iteration 4, LIB1).
// It says what stops working, which labels stop resolving, and whether the app can bring the skill back.
// The restore sentence follows the Archived skills contract: no contract, no promise.
// With the contract, admins restore it themselves; anyone else is pointed to an admin.
const joinNames = (parts) => (parts.length < 2 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`);
const labelNames = (labels) => joinNames((labels || []).filter((label) => label?.name).map((label) => `${label.name} points at v${label.version}`));

// UXF-09: archiving a skill whose protected label points at a version is a
// protected release step on the server (only admins can do it). Say so, built
// only from the labels' own `isProtected` or the given protected names; with
// neither, nothing is claimed.
// eslint-disable-next-line react-refresh/only-export-components -- the sentence is tested on its own.
export function protectedArchiveSentence(labels, protectedLabelNames) {
  const names = (labels || [])
    .filter((label) => label?.name && (label.isProtected === true || (Array.isArray(protectedLabelNames) && protectedLabelNames.includes(label.name))))
    .map((label) => labelDisplayName(label.name));
  if (!names.length) return "";
  return names.length === 1
    ? `${names[0]} is a protected label (only admins can move it), so only workspace admins can archive this skill.`
    : `${joinNames(names)} are protected labels (only admins can move them), so only workspace admins can archive this skill.`;
}

export default function ArchiveConfirmContent({ labels, protectedLabelNames, restoreGate = false, canRestore = false }) {
  const named = (labels || []).filter((label) => label?.name);
  const following = named.length === 1 ? named[0].name : "either label";
  const protectedSentence = protectedArchiveSentence(named, protectedLabelNames);
  return (
    <Stack spacing={1}>
      <Typography variant="s1" color="text.secondary">Agents can no longer fetch it, including through labels.</Typography>
      {named.length > 0 && <Typography variant="s1" color="text.secondary">{`${labelDisplayName(labelNames(named))}. Agents following ${named.length > 2 ? "these labels" : following} will stop receiving this skill.`}</Typography>}
      {protectedSentence && <Typography variant="s1" color="text.secondary" data-testid="archive-protected-rule">{protectedSentence}</Typography>}
      <Typography variant="s1" color="text.secondary">
        {canRestore ? "You can restore it from More → Archived skills." : restoreGate ? "A workspace admin can restore it from More → Archived skills." : "Restoring an archived skill isn't available in this workspace; contact support to restore it."}
      </Typography>
    </Stack>
  );
}
ArchiveConfirmContent.propTypes = { labels: PropTypes.array, protectedLabelNames: PropTypes.arrayOf(PropTypes.string), restoreGate: PropTypes.bool, canRestore: PropTypes.bool };
