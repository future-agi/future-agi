import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import SoonChip from "../../../../components/SoonChip";

/**
 * Vel's Tool calls tab over the real calls. What each call read or wrote and
 * how many rows it returned aren't in the API; its duration is, when the
 * harness timed it.
 */
export default function CallToolCalls({ calls }) {
  if (!calls.length) {
    return (
      <Typography sx={{ p: 2, typography: "s2", color: "text.subtitle" }}>
        The agent made no tool calls.
      </Typography>
    );
  }
  return (
    <Stack>
      {calls.map((call) => (
        <Stack
          key={call.key}
          direction="row"
          spacing={1.5}
          alignItems="flex-start"
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Iconify
            icon="solar:code-linear"
            width={16}
            sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }}
          />
          <Box flex={1} minWidth={0}>
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontFamily: "ui-monospace, Menlo, monospace",
                wordBreak: "break-word",
              }}
            >
              {`${call.name}(${call.args})`}
            </Typography>
            {call.result != null && (
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  wordBreak: "break-word",
                }}
              >
                {`returned ${call.result}`}
              </Typography>
            )}
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ mt: 0.5 }}
            >
              <SoonChip
                label="Read/write"
                what="Whether a tool call read or wrote"
                size="tiny"
              />
              <SoonChip
                label="Rows"
                what="The rows a tool call returned"
                plural
                size="tiny"
              />
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {call.ms == null ? "-" : `${call.ms}ms`}
              </Typography>
            </Stack>
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}
CallToolCalls.propTypes = {
  calls: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      name: PropTypes.string,
      args: PropTypes.string,
      result: PropTypes.string,
      ms: PropTypes.number,
    }),
  ).isRequired,
};
