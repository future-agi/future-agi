import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { useOmegaProducts } from "src/sections/omega/shell/useOmegaProducts";

import {
  SELF_HOSTED_LONGER_COMMANDS,
  commandLimitLine,
  formatSecondsInWords,
  timeLimits,
} from "../lib/runOptions";

/**
 * How long a command may run here, from EE's caps, in plain words. On hosted
 * Future AGI one more line says self-hosted installs can allow longer. With
 * `fact`, the short form for a label / value row ("Up to 15 minutes").
 */
export default function CommandLimit({ caps, fact = false, testId }) {
  const { isHosted } = useOmegaProducts();
  const limits = timeLimits(caps);
  if (!limits) return null;
  if (fact) {
    return (
      <Box component="span" data-testid={testId}>
        Up to {formatSecondsInWords(limits.max)}
        {isHosted && (
          <Typography
            component="span"
            display="block"
            typography="s2"
            color="text.secondary"
          >
            {SELF_HOSTED_LONGER_COMMANDS}
          </Typography>
        )}
      </Box>
    );
  }
  return (
    <Typography typography="s2" color="text.secondary" data-testid={testId}>
      {commandLimitLine(caps)}
      {isHosted ? ` ${SELF_HOSTED_LONGER_COMMANDS}` : ""}
    </Typography>
  );
}

CommandLimit.propTypes = {
  caps: PropTypes.object,
  /** The short form for a label / value row. */
  fact: PropTypes.bool,
  testId: PropTypes.string,
};
