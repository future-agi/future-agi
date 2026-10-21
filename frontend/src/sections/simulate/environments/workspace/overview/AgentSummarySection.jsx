import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { AGENT_SUMMARY_COPY } from "./overview.constants";

export default function AgentSummarySection({ subject }) {
  const title = subject
    ? AGENT_SUMMARY_COPY.agentTitle(subject.name, subject.active_version)
    : AGENT_SUMMARY_COPY.noAgentTitle;
  const body = subject ? null : AGENT_SUMMARY_COPY.portableBody;

  return (
    <Box sx={{ my: 2, p: 2, borderRadius: 1.5, border: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" spacing={2} alignItems="center">
        <Box
          sx={{
            width: 34, height: 34, borderRadius: 1, display: "grid", placeItems: "center",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
            color: "text.secondary", flexShrink: 0,
          }}
        >
          <Iconify icon="solar:cpu-bolt-linear" width={18} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: "fontWeightBold", textTransform: "uppercase", letterSpacing: 0.4 }}>
              {AGENT_SUMMARY_COPY.label}
            </Typography>
            {subject && (
              <Box
                sx={{
                  height: 18, px: 0.75, borderRadius: 0.75,
                  display: "inline-flex", alignItems: "center",
                  bgcolor: (t) => alpha(BUILD_TONES.green, t.palette.mode === "dark" ? 0.16 : 0.1),
                  color: BUILD_TONES.green,
                }}
              >
                <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold" }}>{AGENT_SUMMARY_COPY.connected}</Typography>
              </Box>
            )}
          </Stack>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold", mt: 0.25 }}>{title}</Typography>
          {body && <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{body}</Typography>}
        </Box>
      </Stack>
    </Box>
  );
}
AgentSummarySection.propTypes = {
  subject: PropTypes.shape({
    name: PropTypes.string,
    provider: PropTypes.string,
    versions_count: PropTypes.number,
    active_version: PropTypes.string,
  }),
};
