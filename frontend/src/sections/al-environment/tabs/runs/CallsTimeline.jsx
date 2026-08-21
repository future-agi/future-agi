import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "../../alkTokens";

const TONE = { ok: "divider", refused: "accent.tool", crashed: "accent.fail" };
const MARK_TONE = { ok: "text.secondary", refused: "accent.tool", crashed: "accent.fail" };
const MARK = { ok: "ok", refused: "refused", crashed: "crash" };

/**
 * Every call in order, with what it was given and what came back. This is the half a
 * transcript cannot tell you: what the agent said it did, against what it did.
 */
const CallsTimeline = ({ calls }) => (
  <Stack spacing={0.6} sx={{ py: 0.5 }}>
    {calls.map((call, index) => {
      const state = call.refused ? "refused" : (call.ok ? "ok" : "crashed");
      const args =
        call.arguments && Object.keys(call.arguments).length ? JSON.stringify(call.arguments) : "()";
      const answer = call.refused || !call.ok ? call.error : String(call.result ?? "");
      return (
        <Stack
          // Calls repeat by name within one run, so position is the only stable key.
          // eslint-disable-next-line react/no-array-index-key
          key={index}
          direction="row"
          spacing={2}
          alignItems="flex-start"
          sx={{
            px: 2,
            py: 1.4,
            borderRadius: "5px",
            bgcolor: "action.hover",
            borderLeft: "2px solid",
            borderLeftColor: TONE[state],
          }}
        >
          <Box
            component="span"
            sx={{
              flex: "0 0 3.6rem",
              fontFamily: ALK_MONO,
              fontSize: 10.5,
              letterSpacing: "0.05em",
              textTransform: "uppercase",
              color: MARK_TONE[state],
              pt: "2px",
            }}
          >
            {MARK[state]}
          </Box>
          <Box sx={{ minWidth: 0, flex: "1 1 auto" }}>
            <Box
              component="span"
              sx={{ fontFamily: ALK_MONO, fontSize: 12.8, fontWeight: 600, color: "text.primary" }}
            >
              {call.name}
            </Box>
            <Box
              component="code"
              sx={{
                fontFamily: ALK_MONO,
                fontSize: 11.7,
                color: "text.secondary",
                ml: 1.4,
                wordBreak: "break-all",
              }}
            >
              {args}
            </Box>
            {answer && (
              <Typography
                sx={{ fontSize: 12, color: "text.secondary", mt: 0.6, wordBreak: "break-word" }}
              >
                {/* A tool can return a page of JSON; the timeline is a scan, not the payload. */}
                {String(answer).slice(0, 400)}
              </Typography>
            )}
          </Box>
        </Stack>
      );
    })}
  </Stack>
);

CallsTimeline.propTypes = { calls: PropTypes.array.isRequired };

export default CallsTimeline;
