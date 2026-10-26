import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { HintLine } from "./replicationBits";

const MONO = "ui-monospace, Menlo, monospace";

/*
  The optional sealed path for Vapi and Retell: import a copy of the
  assistant into our sandbox and upload the code behind its tools, so the
  tools answer from the seeded world instead of your real systems. Built-in
  platform actions aren't tool code, so they still run for real.

  Not part of connecting — a connected agent is tested live, and this is
  offered later, from the agent's Connections.
*/
export default function SandboxImport({
  providerName,
  on,
  onToggle,
  files,
  onFiles,
}) {
  const add = (list) => {
    const added = Array.from(list || []).map((f) => ({
      name: f.name,
      size: f.size,
    }));
    onFiles([
      ...files,
      ...added.filter((a) => !files.some((f) => f.name === a.name)),
    ]);
  };
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 1.75, py: 1.25 }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Test a sealed copy instead
          </Typography>
          <Typography
            component="div"
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
          >
            <HintLine
              hint={`We import a copy of the ${providerName || "platform"} assistant into our sandbox, so results carry Sealed proof.`}
            >
              Optional · import a copy and upload the code behind its tools —
              they answer from the seeded world
            </HintLine>
          </Typography>
        </Box>
        <Button
          size="small"
          variant={on ? "outlined" : "text"}
          onClick={() => onToggle(!on)}
          sx={{ flexShrink: 0, color: on ? "text.primary" : "text.secondary" }}
        >
          {on ? "Don't import" : "Import a copy"}
        </Button>
      </Stack>
      {on && (
        <Stack
          spacing={1}
          sx={{
            px: 1.75,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Button
              component="label"
              size="small"
              variant="outlined"
              startIcon={
                <Iconify icon="solar:cloud-upload-linear" width={14} />
              }
            >
              Choose tool code
              <input
                hidden
                multiple
                type="file"
                onChange={(e) => add(e.target.files)}
              />
            </Button>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              The handlers your tools&apos; server URLs call · .py .ts .js .zip
            </Typography>
          </Stack>
          {files.map((f) => (
            <Stack
              key={f.name}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1,
                py: 0.5,
                borderRadius: 1,
                bgcolor: (t) => alpha(t.palette.text.primary, 0.04),
              }}
            >
              <Iconify
                icon="solar:document-linear"
                width={14}
                sx={{ color: "text.subtitle" }}
              />
              <Typography
                noWrap
                sx={{
                  typography: "s2",
                  fontFamily: MONO,
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {f.name}
              </Typography>
              <IconButton
                size="small"
                onClick={() => onFiles(files.filter((x) => x.name !== f.name))}
              >
                <Iconify
                  icon="solar:trash-bin-minimalistic-linear"
                  width={14}
                  sx={{ color: "text.subtitle" }}
                />
              </IconButton>
            </Stack>
          ))}
          <Typography
            component="div"
            sx={{ typography: "s3", color: "text.secondary" }}
          >
            <HintLine hint="Transfers, end call and voicemail still run for real in an imported copy.">
              Built-in platform actions still run for real.
            </HintLine>
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
SandboxImport.propTypes = {
  providerName: PropTypes.string,
  on: PropTypes.bool,
  onToggle: PropTypes.func,
  files: PropTypes.array,
  onFiles: PropTypes.func,
};
