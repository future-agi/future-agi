import PropTypes from "prop-types";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import Section from "src/components/section";

export default function CapabilityChanges({ changes, onOpenFile }) {
  return (
    <Section title="Execution-related changes">
      <Stack spacing={1.5}>
        <Typography variant="s3" color="text.secondary">
          Declared permissions are not a sandbox. Unrecognized behavior may
          still exist. Opening this review executes nothing.
        </Typography>
        {!changes ? (
          <Alert severity="info">
            Capability comparison unavailable. No safety conclusion can be
            drawn.
          </Alert>
        ) : (
          <>
            <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>
              Before {changes.beforeDigest} · candidate {changes.afterDigest}
            </Typography>
            {!changes.changes.length && (
              <Typography variant="s1">
                No changes in recognized static declarations. This is not a
                safety certification.
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
