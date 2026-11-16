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

export default function RunSeedLine({ seed, value, error, onChange }) {
  const [editing, setEditing] = useState(false);
  const open = editing || Boolean(error);
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
          disabled={Boolean(error)}
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
          error={Boolean(error)}
          helperText={error || COPY.seedHelp}
          inputProps={{ inputMode: "numeric", "data-testid": "run-seed" }}
          sx={{ mt: 1 }}
        />
      </Collapse>
    </Box>
  );
}

RunSeedLine.propTypes = {
  seed: PropTypes.string.isRequired,
  // Null for a new seed.
  value: PropTypes.string,
  error: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};
