import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import CodeIcon from "./CodeIcon";
import { KEY_ENV, maskValue } from "./curl";
import { NO_RECORDING_CLASS } from "./noRecording";

const mono = { fontFamily: "monospace", fontSize: 12.5 };

/**
 * Which key the snippet carries, and the two explicit steps around it: create
 * one (its secret exists only in that reply), and reveal it before Copy may
 * carry it. Without a held key the snippet reads the key from the shell.
 */
export default function KeyStrip({
  heldKey,
  revealed,
  onToggleReveal,
  onForget,
  onCreate,
  creating,
  createFailed,
}) {
  const keysLink = (
    <Link
      component={RouterLink}
      href={paths.dashboard.keys}
      underline="hover"
      data-testid="code-keys-link"
    >
      API keys
    </Link>
  );

  if (!heldKey) {
    return (
      <Box
        className={NO_RECORDING_CLASS}
        data-testid="code-key-strip"
        data-key-state="none"
        sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 1,
          }}
        >
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ minWidth: 0 }}
          >
            Signed with your personal API key, read from{" "}
            <Box component="code" sx={mono}>
              ${KEY_ENV.API}
            </Box>{" "}
            and{" "}
            <Box component="code" sx={mono}>
              ${KEY_ENV.SECRET}
            </Box>
            .
          </Typography>
          <LoadingButton
            size="small"
            variant="outlined"
            color="inherit"
            loading={creating}
            onClick={onCreate}
            startIcon={<CodeIcon name="key" />}
            data-testid="code-create-key"
          >
            Create a key
          </LoadingButton>
        </Box>
        <Typography typography="s3" color="text.secondary">
          A new key&apos;s secret is shown only here, to you, in this tab, until
          you sign out, reload or forget it. Your keys are listed in {keysLink}.
        </Typography>
        {createFailed && (
          <Typography
            typography="s3"
            color="error.main"
            data-testid="code-create-key-error"
          >
            The key was not created. Try again, or create one in {keysLink}.
          </Typography>
        )}
      </Box>
    );
  }

  return (
    <Box
      className={NO_RECORDING_CLASS}
      data-testid="code-key-strip"
      data-key-state={revealed ? "revealed" : "masked"}
      sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 1,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            minWidth: 0,
            color: "text.secondary",
          }}
        >
          <CodeIcon name="key" />
          <Typography
            typography="s2"
            color="text.primary"
            sx={{ minWidth: 0, overflowWrap: "anywhere" }}
            data-testid="code-key-name"
          >
            {heldKey.name || "Your API key"}
          </Typography>
          <Box component="code" sx={{ ...mono, color: "text.secondary" }}>
            {maskValue(heldKey.apiKey)}
          </Box>
        </Box>
        <Box sx={{ display: "flex", gap: 0.5 }}>
          <Button
            size="small"
            color="inherit"
            onClick={onToggleReveal}
            startIcon={<CodeIcon name={revealed ? "eyeOff" : "eye"} />}
            aria-pressed={revealed}
            data-testid="code-reveal-key"
          >
            {revealed ? "Hide key" : "Reveal key"}
          </Button>
          <Button
            size="small"
            color="inherit"
            onClick={onForget}
            data-testid="code-forget-key"
          >
            Forget
          </Button>
        </Box>
      </Box>
      <Typography
        typography="s3"
        color="text.secondary"
        data-testid="code-key-kept"
      >
        Kept in this tab, for you only, until you sign out, reload or forget it.
        Disable it any time in {keysLink}.
      </Typography>
    </Box>
  );
}

KeyStrip.propTypes = {
  heldKey: PropTypes.shape({
    name: PropTypes.string,
    apiKey: PropTypes.string.isRequired,
  }),
  revealed: PropTypes.bool,
  onToggleReveal: PropTypes.func.isRequired,
  onForget: PropTypes.func.isRequired,
  onCreate: PropTypes.func.isRequired,
  creating: PropTypes.bool,
  createFailed: PropTypes.bool,
};
