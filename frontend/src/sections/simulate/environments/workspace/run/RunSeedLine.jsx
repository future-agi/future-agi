import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Collapse,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { RUN_DIALOG_COPY as COPY } from "../workspace.constants";

/**
 * The run's seed folded to one line: "New seed" or the seed, with Edit to type one. A seed that can't be read stays
 * open, so its message shows.
 */
export default function RunSeedLine({ seed, value, invalid, onChange }) {
  const [editing, setEditing] = useState(false);
  const open = editing || invalid;
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
          {COPY.seedLine}
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
          data-testid="run-seed-line"
        >
          {value || COPY.seedNew}
        </Typography>
        <Button
          size="small"
          color="inherit"
          disabled={invalid}
          onClick={() => setEditing((shown) => !shown)}
        >
          {open ? COPY.done : COPY.edit}
        </Button>
      </Stack>
      <Collapse in={open}>
        <TextField
          fullWidth
          size="small"
          label={COPY.seed}
          value={seed}
          onChange={(event) => onChange(event.target.value)}
          error={invalid}
          helperText={invalid ? COPY.seedInvalid : COPY.seedHelp}
          inputProps={{ inputMode: "numeric", "data-testid": "run-seed" }}
          sx={{ mt: 1 }}
        />
      </Collapse>
    </Box>
  );
}

RunSeedLine.propTypes = {
  // The seed as typed.
  seed: PropTypes.string.isRequired,
  // The seed the run would send, as decimal text; null for a new one.
  value: PropTypes.string,
  invalid: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};
