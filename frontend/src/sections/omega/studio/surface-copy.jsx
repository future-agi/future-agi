import React from "react";
import Box from "@mui/material/Box";

import { DIAGNOSIS, diagnosisCopy } from "src/sections/omega/embed";

import CopyableCommand from "./CopyableCommand";
import {
  DIAGNOSIS_COPY,
  GATE_COPY,
  STUDIO_LABEL,
  omegaDevCommand,
} from "./studio-copy";

// A sentence, then the exact command under it with a copy button.
function withCommand(sentence, command) {
  return (
    <>
      {sentence}
      <Box
        component="span"
        data-testid="studio-gate-command"
        sx={{ display: "block", mt: 1.5, textAlign: "left" }}
      >
        <CopyableCommand command={command} testId="studio-gate-fix" />
      </Box>
    </>
  );
}

// EmbeddedSurface `copy` overrides: the same gate panel as every framed
// surface, in Studio's words and with the exact fix. A saved URL has no key:
// the child will ask for a freshly opened/pasted link, not recover storage.
export function surfaceCopy(origin) {
  const command = omegaDevCommand(origin);
  return {
    connecting: GATE_COPY.connecting,
    checking: GATE_COPY.checking,
    needsKey: GATE_COPY.needsKey,
    refused: GATE_COPY.refused,
    diagnosis: (cause, { origin: server }) => {
      switch (cause) {
        case DIAGNOSIS.framingBlocked: {
          const text = DIAGNOSIS_COPY.framingBlocked(server);
          return { ...text, body: withCommand(text.body, command) };
        }
        case DIAGNOSIS.notRunning: {
          const text = DIAGNOSIS_COPY.notRunning(server);
          return { ...text, body: withCommand(text.body, command) };
        }
        case DIAGNOSIS.safari: {
          const text = diagnosisCopy(cause, {
            label: STUDIO_LABEL,
            origin: server,
          });
          return {
            ...text,
            body: `${text.body} ${DIAGNOSIS_COPY.safariAlternative}`,
          };
        }
        default:
          return diagnosisCopy(cause, { label: STUDIO_LABEL, origin: server });
      }
    },
  };
}
