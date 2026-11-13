import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import IdentityChip from '../components/IdentityChip';
import CapabilityNotice from '../components/CapabilityNotice';
import Section from "src/components/section";

export default function CapabilityChanges({ changes, onOpenFile }) {
  return (
    <Section title="Execution-related changes">
      <Stack spacing={1.5}>
        {!changes ? (
          <CapabilityNotice title="Permission comparison unavailable" description="Review the file changes before deciding." />
        ) : (
          <>
            <Stack direction="row" flexWrap="wrap" gap={1}><IdentityChip label="Before" value={changes.beforeDigest} /><IdentityChip label="Candidate" value={changes.afterDigest} /></Stack>
            {!changes.changes.length && (
              <Typography variant="s1">
                No changes in recognized static declarations. This is not a safety certification.
              </Typography>
            )}
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
          </>
        )}
      </Stack>
    </Section>
  );
}
CapabilityChanges.propTypes = {
  changes: PropTypes.object,
  onOpenFile: PropTypes.func,
};
