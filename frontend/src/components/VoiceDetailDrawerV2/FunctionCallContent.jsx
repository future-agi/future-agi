import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const displayValue = (value) =>
  typeof value === "string" ? value : JSON.stringify(value);

export default function FunctionCallContent({ calls, renderText }) {
  return (
    <Stack spacing={1.5} sx={{ py: 0.5, minWidth: 0 }}>
      {calls.map((call, index) => {
        const name = call.name || call.function?.name || "tool";
        const duration = call.duration_ms ?? call.durationMs;
        const args = call.arguments ?? call.function?.arguments;
        const result = call.result ?? call.output;
        return (
          <Box key={call.id || `${name}-${index}`}>
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Iconify
                icon="mdi:tools"
                width={18}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              <Typography sx={{ fontSize: 12.5, overflowWrap: "anywhere" }}>
                <Box component="span" sx={{ fontWeight: 600 }}>
                  Function call
                </Box>
                {" · "}
                <Box component="span" sx={{ fontFamily: "monospace" }}>
                  {renderText(name)}
                </Box>
                {duration != null && ` · ${duration}ms`}
              </Typography>
            </Stack>
            <Box
              sx={{
                mt: 1,
                fontFamily: "monospace",
                fontSize: 11.5,
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                wordBreak: "normal",
              }}
            >
              {args != null && (
                <Box>{renderText(`→ args: ${displayValue(args)}`)}</Box>
              )}
              {result != null && (
                <Box>{renderText(`← result: ${displayValue(result)}`)}</Box>
              )}
            </Box>
          </Box>
        );
      })}
    </Stack>
  );
}

FunctionCallContent.propTypes = {
  calls: PropTypes.arrayOf(PropTypes.object).isRequired,
  renderText: PropTypes.func.isRequired,
};
