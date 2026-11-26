import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Collapse,
  Typography,
  useMediaQuery,
} from "@mui/material";

import { RUN_DIALOG_COPY as COPY } from "../workspace.constants";

// The run plan's warnings that saved changes moved what some scenarios are graded on since they were proved.
// The sentence names at most five; `scenario_keys` carries every one, listed one click away.
export default function ChangedSinceProvedBox({ warnings }) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [showKeys, setShowKeys] = useState(false);
  if (!warnings.length) return null;
  const keys = [...new Set(warnings.flatMap((one) => one.scenario_keys ?? []))];
  return (
    <Box
      data-testid="run-changed-since-proved"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.5,
      }}
    >
      <Typography
        sx={{ typography: "s1", fontWeight: "fontWeightMedium", mb: 0.5 }}
      >
        {COPY.changedTitle}
      </Typography>
      {warnings.map((one) => (
        <Typography
          key={one.piece || one.code}
          sx={{ typography: "s1", color: "text.secondary" }}
        >
          {one.message}
        </Typography>
      ))}
      <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
        {COPY.changedHint}
      </Typography>
      {keys.length > 0 && (
        <Button
          size="small"
          color="inherit"
          sx={{ mt: 0.5 }}
          onClick={() => setShowKeys((open) => !open)}
        >
          {showKeys ? COPY.hideWhich : COPY.showWhich}
        </Button>
      )}
      <Collapse
        in={showKeys}
        timeout={reduceMotion ? 0 : undefined}
        unmountOnExit
      >
        <Box
          component="ul"
          sx={{ m: 0, pl: 2, typography: "s3", color: "text.secondary" }}
        >
          {keys.map((key) => (
            <li key={key}>{key}</li>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}

ChangedSinceProvedBox.propTypes = {
  // The run plan's scenarios_need_revalidation warnings.
  warnings: PropTypes.arrayOf(
    PropTypes.shape({
      code: PropTypes.string,
      piece: PropTypes.string,
      message: PropTypes.string,
      scenario_keys: PropTypes.arrayOf(PropTypes.string),
    }),
  ).isRequired,
};
