import PropTypes from "prop-types";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import OriginChip from "../../components/OriginChip";
import SideDrawer from "../../components/SideDrawer";
import { MONO_FONT } from "../contract/contract.constants";
import { AGENT_SUMMARY_COPY } from "./overview.constants";

export default function PromptDrawer({
  open,
  onClose,
  agentName,
  prompt,
  origin,
}) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      enqueueSnackbar(AGENT_SUMMARY_COPY.copied, { variant: "info" });
    } catch {
      enqueueSnackbar(AGENT_SUMMARY_COPY.copyFailed, { variant: "error" });
    }
  };
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 2,
            pr: 6,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>
              {AGENT_SUMMARY_COPY.promptTitle(agentName)}
            </Typography>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ mt: 0.5 }}
            >
              {origin && (
                <OriginChip
                  origin={origin.kind}
                  file={origin.path || origin.field}
                  line={origin.line ?? undefined}
                />
              )}
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {AGENT_SUMMARY_COPY.promptLines(prompt.split("\n").length)}
              </Typography>
            </Stack>
          </Box>
          <Tooltip arrow title={AGENT_SUMMARY_COPY.copyPrompt}>
            <IconButton
              size="small"
              onClick={copy}
              aria-label={AGENT_SUMMARY_COPY.copyPrompt}
            >
              <Iconify
                icon="solar:copy-linear"
                width={16}
                sx={{ color: "text.subtitle" }}
              />
            </IconButton>
          </Tooltip>
        </Stack>
        <Box
          sx={{
            flex: 1,
            overflow: "auto",
            m: 2.5,
            p: 2,
            borderRadius: 1,
            bgcolor: "background.neutral",
          }}
        >
          <Typography
            component="pre"
            sx={{
              typography: "s2",
              fontFamily: MONO_FONT,
              whiteSpace: "pre-wrap",
              m: 0,
            }}
          >
            {prompt}
          </Typography>
        </Box>
      </Stack>
    </SideDrawer>
  );
}
PromptDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  agentName: PropTypes.string,
  prompt: PropTypes.string.isRequired,
  origin: PropTypes.shape({
    kind: PropTypes.string,
    path: PropTypes.string,
    field: PropTypes.string,
    line: PropTypes.number,
  }),
};
