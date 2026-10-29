import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { providerEventsOf } from "../_mock/providerEvents";

const MONO = "ui-monospace, Menlo, monospace";
const fmt = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/**
 * Provider events for one call: each event the platform sent, when it
 * arrived and how it was matched — a redelivered webhook is shown, marked
 * duplicate, and never became a second trial.
 */
export default function ProviderEvents({ task, provider }) {
  const { callId, events } = providerEventsOf(task, provider);
  const dupes = events.filter((e) => e.duplicate).length;
  return (
    <Stack spacing={1} sx={{ p: 2 }}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`${events.length} events from ${provider ? provider.charAt(0).toUpperCase() + provider.slice(1) : "the platform"} · call `}
        <Box component="span" sx={{ fontFamily: MONO }}>
          {callId}
        </Box>
        {dupes ? ` · ${dupes} duplicate, matched to this trial` : ""}
      </Typography>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {events.map((e, i) => (
          <Stack
            key={`${e.type}-${i}`}
            direction="row"
            spacing={1.5}
            alignItems="baseline"
            sx={{
              px: 1.25,
              py: 0.75,
              "& + &": { borderTop: "1px solid", borderColor: "divider" },
              ...(e.duplicate && { bgcolor: "action.hover" }),
            }}
          >
            <Typography
              sx={{
                typography: "s3",
                fontFamily: MONO,
                color: "text.tertiary",
                width: 40,
                flexShrink: 0,
              }}
            >
              {fmt(e.at)}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                fontFamily: MONO,
                width: 110,
                flexShrink: 0,
              }}
            >
              {e.type}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: e.duplicate ? "text.secondary" : "text.primary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {e.duplicate ? `Duplicate — ${e.line}` : e.line}
            </Typography>
          </Stack>
        ))}
      </Box>
    </Stack>
  );
}
ProviderEvents.propTypes = {
  task: PropTypes.object.isRequired,
  provider: PropTypes.string,
};
