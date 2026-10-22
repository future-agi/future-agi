import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { CopyField } from "../components/primitives";
import { HintLine } from "../agents/connect/replicationBits";

/**
 * Numbered terminal steps — a step's title, one line on what it does and the
 * command to copy, joined by a rule. The prebuilt world's Build locally and
 * Your own machine both read this way.
 */
export default function LocalSteps({ steps }) {
  return (
    <Stack spacing={0}>
      {steps.map((s, i) => (
        <Stack key={s.title} direction="row" spacing={1.75}>
          <Stack alignItems="center" sx={{ flexShrink: 0 }}>
            <Box
              sx={{
                width: 24, height: 24, borderRadius: "50%", display: "grid", placeItems: "center",
                border: "1px solid", borderColor: "divider",
                typography: "s3", fontWeight: 700, color: "text.secondary",
              }}
            >
              {i + 1}
            </Box>
            {i < steps.length - 1 && <Box sx={{ flex: 1, width: "1px", bgcolor: "divider", my: 0.75, minHeight: 24 }} />}
          </Stack>
          <Box sx={{ flex: 1, minWidth: 0, pb: i < steps.length - 1 ? 2.25 : 0 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>{s.title}</Typography>
            <Typography component="div" sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
              {s.hint ? <HintLine hint={s.hint}>{s.body}</HintLine> : s.body}
            </Typography>
            {s.extra}
            <CopyField value={s.cmd} wrap onCopy={s.onCopy} />
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}
LocalSteps.propTypes = {
  /* [{ title, body, hint?, cmd, extra?, onCopy? }] — `extra` sits above the command (e.g. an OS picker). */
  steps: PropTypes.arrayOf(PropTypes.object).isRequired,
};
