import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { sharedStateOf } from "../_mock/sharedState";

const mono = { fontFamily: "ui-monospace, Menlo, monospace" };
const COLS = "minmax(0,1.2fr) minmax(0,1fr) minmax(0,1.3fr)";

/**
 * Shared state — shown only when two of a multi-agent app's workers wrote the
 * same record in this trial: who wrote, and which write committed. The final
 * check is a separate result.
 */
export default function SharedState({ agent, task, sx }) {
  const s = sharedStateOf({ agent, task });
  if (!s) return null;
  return (
    <Stack spacing={0.75} sx={sx}>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        Shared state
      </Typography>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `minmax(0,0.9fr) ${COLS}`,
            columnGap: 1.5,
            px: 1.5,
            py: 0.625,
            typography: "s3",
            color: "text.tertiary",
          }}
        >
          <Box>Record</Box>
          <Box>Participant</Box>
          <Box>Write</Box>
          <Box>Result</Box>
        </Box>
        {s.rows.map((r) => (
          <Box
            key={r.participant + r.result}
            sx={{
              display: "grid",
              gridTemplateColumns: `minmax(0,0.9fr) ${COLS}`,
              columnGap: 1.5,
              px: 1.5,
              py: 0.75,
              typography: "s2",
            }}
          >
            <Box sx={mono}>{s.record}</Box>
            <Box
              sx={{
                color:
                  r.participant === "Unknown"
                    ? "text.tertiary"
                    : "text.primary",
              }}
            >
              {r.participant}
            </Box>
            <Box sx={mono}>{r.write}</Box>
            <Box>
              {r.result}
              {r.note && (
                <Box component="span" sx={{ color: "text.tertiary" }}>
                  {` (${r.note})`}
                </Box>
              )}
            </Box>
          </Box>
        ))}
      </Box>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`The final check is separate: ${s.final}`}
      </Typography>
    </Stack>
  );
}
SharedState.propTypes = {
  agent: PropTypes.object,
  task: PropTypes.object,
  sx: PropTypes.object,
};
