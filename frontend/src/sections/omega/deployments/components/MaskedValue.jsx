import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";

const MASK = "••••••••";

/**
 * A deployment variable's value, masked until the reader asks for it. Every
 * workspace member can open a deployment's Settings, and a variable often
 * holds a key someone pasted there instead of the vault, so the value is
 * never on screen (or in a screenshot) by default. Each value is revealed on
 * its own; nothing is remembered across pages.
 */
export default function MaskedValue({ name, value }) {
  const [shown, setShown] = useState(false);
  const empty = value === "" || value === null || value === undefined;
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "baseline",
        gap: 1,
        minWidth: 0,
      }}
    >
      <Box
        component="span"
        data-testid="env-value"
        data-masked={shown || empty ? "false" : "true"}
        sx={{ overflowWrap: "anywhere" }}
      >
        {empty ? "(empty)" : shown ? value : MASK}
      </Box>
      {!empty && (
        <Button
          size="small"
          color="inherit"
          onClick={() => setShown((now) => !now)}
          aria-label={`${shown ? "Hide" : "Show"} the value of ${name}`}
          aria-pressed={shown}
          data-testid="env-value-toggle"
          sx={{
            minWidth: 0,
            px: 0.75,
            py: 0,
            typography: "s3",
            color: "text.secondary",
          }}
        >
          {shown ? "Hide" : "Show"}
        </Button>
      )}
    </Box>
  );
}

MaskedValue.propTypes = {
  name: PropTypes.string.isRequired,
  value: PropTypes.string,
};
