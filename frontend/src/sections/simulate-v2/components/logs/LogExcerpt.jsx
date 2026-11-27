import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import { errorExcerptOf } from "../../_mock/sandboxLogs";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * The failing step of a sandbox log and a few redacted lines around its
 * first error, with a button to open the whole log.
 */
export default function LogExcerpt({ log, onViewAll }) {
  const excerpt = useMemo(() => errorExcerptOf(log), [log]);
  if (!log) return null;
  return (
    <Box data-testid="log-excerpt">
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.75 }}>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {!excerpt
            ? "Sandbox log"
            : excerpt.step.id === "trial"
              ? "Error in this trial"
              : `Failed at ${excerpt.step.label}`}
        </Typography>
        {onViewAll && (
          <Button
            size="small"
            variant="outlined"
            onClick={onViewAll}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            View full log
          </Button>
        )}
      </Stack>
      {excerpt && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            bgcolor: "background.neutral",
            px: 1.5,
            py: 1,
            maxHeight: 200,
            overflow: "auto",
          }}
        >
          <Typography
            component="pre"
            sx={{
              fontFamily: MONO,
              fontSize: 12,
              fontWeight: 400,
              lineHeight: "18px",
              color: "text.primary",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              m: 0,
            }}
          >
            {excerpt.lines.map((l) => l.text).join("\n")}
          </Typography>
        </Box>
      )}
    </Box>
  );
}

LogExcerpt.propTypes = {
  /* A log from buildLogOf / checkLogOf. */
  log: PropTypes.object,
  /* Opens the whole log; left out where the whole log is already below. */
  onViewAll: PropTypes.func,
};
