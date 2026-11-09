import React, { useState } from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import OmegaEmptyState from "src/sections/omega/shell/OmegaEmptyState";

import { VIEWER_COPY } from "./console-copy";
import { MINT_STATE } from "./session-assertion";

// What a viewer sees at /dashboard/console/* instead of the frame (CP P10):
// no frame is created, so no key is minted and nothing loads from the Console.
// "Check again" asks the session exchange once (a role may have changed since
// the page loaded); the key it returns is never used and simply expires. Any
// 403 means "not admitted": the exchange's own viewer reason, or the plain
// permission_denied Future AGI's workspace write guard answers a viewer's POST
// with before the exchange runs (seen on the live stack). Only no answer, a
// 5xx or a 429 is "couldn't check".
const NOT_ADMITTED = new Set([MINT_STATE.VIEWER, MINT_STATE.REFUSED]);

export default function ConsoleViewerPanel({ checkAccess, onAllowed }) {
  const [state, setState] = useState("idle");

  const check = async () => {
    setState("checking");
    try {
      await checkAccess();
      setState("idle");
      onAllowed();
    } catch (error) {
      setState(NOT_ADMITTED.has(error?.state) ? "still" : "failed");
    }
  };

  const note =
    state === "still"
      ? VIEWER_COPY.still
      : state === "failed"
        ? VIEWER_COPY.failed
        : null;

  return (
    <Stack
      data-testid="omega-console-viewer"
      data-state={state}
      sx={{ flex: 1, minHeight: 0 }}
    >
      <OmegaEmptyState
        icon="console"
        // The panel is the whole page, so its title is the page's heading.
        headingComponent="h1"
        title={VIEWER_COPY.title}
        description={VIEWER_COPY.body}
        action={
          <Stack spacing={1} alignItems="center">
            <Button
              variant="outlined"
              size="small"
              color="inherit"
              onClick={check}
              disabled={state === "checking"}
            >
              {state === "checking" ? VIEWER_COPY.checking : VIEWER_COPY.check}
            </Button>
            {note && (
              <Typography
                variant="s2"
                color="text.secondary"
                aria-live="polite"
                data-testid="omega-console-viewer-note"
              >
                {note}
              </Typography>
            )}
          </Stack>
        }
      />
    </Stack>
  );
}

ConsoleViewerPanel.propTypes = {
  /** Resolves when the exchange now admits this user; rejects with its error. */
  checkAccess: PropTypes.func.isRequired,
  onAllowed: PropTypes.func.isRequired,
};
