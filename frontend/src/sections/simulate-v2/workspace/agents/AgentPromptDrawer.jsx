import PropTypes from "prop-types";
import { useState } from "react";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";

/**
 * The prompt an agent version runs with, read-only.
 *
 * Everything the build derived — tools, rules, the scenarios that press on
 * them — traces back to this text, so it has to be one click away from the
 * build and from the test subject afterwards.
 */
export default function AgentPromptDrawer({
  open,
  onClose,
  label = "v1",
  prompt,
}) {
  const [copied, setCopied] = useState(false);
  const text = prompt?.text || "";
  const lines = text ? text.split("\n").length : 0;

  const copy = () => {
    navigator.clipboard?.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", md: 640 }}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography
              sx={{ typography: "m2", fontWeight: 600 }}
            >{`Agent ${label} · prompt`}</Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.125 }}
            >
              {prompt?.origin || "No prompt on record"}
              {lines ? ` · ${lines} lines` : ""}
            </Typography>
          </Box>
          {text && (
            <Tooltip arrow title={copied ? "Copied" : "Copy prompt"}>
              <IconButton size="small" onClick={copy}>
                <Iconify
                  icon={
                    copied ? "solar:check-read-linear" : "solar:copy-linear"
                  }
                  width={16}
                />
              </IconButton>
            </Tooltip>
          )}
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, overflowY: "auto", p: 2.5 }}>
          {text ? (
            <Box
              component="pre"
              sx={{
                m: 0,
                p: 2,
                borderRadius: 1,
                bgcolor: "background.neutral",
                border: "1px solid",
                borderColor: "divider",
                fontFamily: "ui-monospace, Menlo, monospace",
                fontSize: 13,
                lineHeight: 1.65,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                color: "text.primary",
              }}
            >
              {text}
            </Box>
          ) : (
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              This version was attached without a prompt we could read.
            </Typography>
          )}
          {text && !prompt?.submitted && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 1.5 }}
            >
              The tools on the World tab and the rules on the Contract tab were
              read from this prompt and the code around it.
            </Typography>
          )}
        </Box>
      </Stack>
    </SideDrawer>
  );
}

AgentPromptDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  label: PropTypes.string,
  prompt: PropTypes.shape({
    text: PropTypes.string,
    origin: PropTypes.string,
    submitted: PropTypes.bool,
  }),
};
