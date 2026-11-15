import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import IdentityChip from '../components/IdentityChip';
import CapabilityNotice from '../components/CapabilityNotice';
import TechnicalDetails from '../components/TechnicalDetails';

// Permissions detail. The headline ("No permission changes found" or a count) lives in the review
// summary table, so this renders only what that sentence cannot say: each changed declaration, and
// the compared fingerprints inside "What was compared", which bind what the reviewer saw to this pair.
export default function CapabilityChanges({ changes, onOpenFile, summary = false }) {
  if (!changes) return <CapabilityNotice title="Permission comparison unavailable" description="Review the file changes before deciding." />;
  return (
    <Stack spacing={1.5}>
      {summary && <Typography variant="s1">{changes.changes.length ? `${changes.changes.length} permission ${changes.changes.length === 1 ? 'change' : 'changes'}` : 'No permission changes found.'}</Typography>}
      {changes.changes.map((item) => (
        <Box key={`${item.path}:${item.category}:${item.name}`}>
          <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>
            {item.path} · {item.category}
            {item.name ? ` / ${item.name}` : ""} · {item.change}
          </Typography>
          <Typography
            variant="s1"
            component="pre"
            sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
          >{`Before: ${JSON.stringify(item.before) ?? "absent"}\nAfter: ${JSON.stringify(item.after) ?? "absent"}`}</Typography>
          {onOpenFile && (
            <Button size="small" onClick={() => onOpenFile(item.path)}>
              Inspect exact file change
            </Button>
          )}
        </Box>
      ))}
      <TechnicalDetails title="What was compared">
        <Stack spacing={1}>
          <Typography variant="s3">{changes.changes.length ? "Only recognized static declarations are compared. This is not a safety certification." : "No changes in recognized static declarations. This is not a safety certification."}</Typography>
          <Stack direction="row" flexWrap="wrap" gap={1}>
            <IdentityChip inline label="Before" value={changes.beforeDigest} />
            <IdentityChip inline label="Candidate" value={changes.afterDigest} />
          </Stack>
        </Stack>
      </TechnicalDetails>
    </Stack>
  );
}
CapabilityChanges.propTypes = {
  changes: PropTypes.object,
  onOpenFile: PropTypes.func,
  summary: PropTypes.bool,
};
