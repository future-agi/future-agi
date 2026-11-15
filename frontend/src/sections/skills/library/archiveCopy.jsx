import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";

// What the Archive confirm tells the person before the one-way step (UX iteration 4, LIB1).
// It says what stops working, which labels stop resolving, and whether the app can bring the skill back.
// The restore sentence follows the Archived skills binding: no binding, no promise.
const labelNames = (labels) => {
  const parts = (labels || []).filter((label) => label?.name).map((label) => `${label.name} points at v${label.version}`);
  if (parts.length < 2) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
};

export default function ArchiveConfirmContent({ labels, canRestore }) {
  const named = (labels || []).filter((label) => label?.name);
  const following = named.length === 1 ? named[0].name : "either label";
  return (
    <Stack spacing={1}>
      <Typography variant="s1" color="text.secondary">Agents can no longer fetch it, including through labels.</Typography>
      {named.length > 0 && <Typography variant="s1" color="text.secondary">{`${labelNames(named)}. Agents following ${named.length > 2 ? "these labels" : following} will stop receiving this skill.`}</Typography>}
      <Typography variant="s1" color="text.secondary">
        {canRestore ? "You can restore it from More → Archived skills." : "Restoring an archived skill isn't available in this workspace; contact support to restore it."}
      </Typography>
    </Stack>
  );
}
ArchiveConfirmContent.propTypes = { labels: PropTypes.array, canRestore: PropTypes.bool };
