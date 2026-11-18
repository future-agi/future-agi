import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Dialog, DialogTitle, DialogContent, DialogActions, TextField, Button, Stack,
} from "@mui/material";
import { useRenameEnvironment } from "src/api/simulate-environments/environments";
import { validateEnvName, MAX_ENV_NAME } from "./renameEnvironment";

/**
 * §8 rename dialog. Shared by the workspace header (pencil affordance). The
 * response is the full §6 detail body, so the mutation seeds the detail cache;
 * this dialog only validates, submits, and surfaces the error inline.
 */
export default function RenameEnvironmentDialog({ open, env, onClose }) {
  const [name, setName] = useState(env?.name || "");
  const [error, setError] = useState(null);
  const rename = useRenameEnvironment();

  // Reset the field to the current name each time the dialog opens.
  useEffect(() => {
    if (open) {
      setName(env?.name || "");
      setError(null);
    }
  }, [open, env?.name]);

  const submit = () => {
    // Enter bypasses the disabled Save button, so guard the in-flight case here
    // too — otherwise a second Enter double-submits the rename.
    if (rename.isPending) return;
    const check = validateEnvName(name);
    if (!check.ok) {
      setError(check.error);
      return;
    }
    setError(null);
    rename.mutate(
      { id: env.id, name: check.value },
      {
        onSuccess: () => onClose?.(),
        onError: (e) =>
          setError(e?.message || "Couldn't rename the environment. Try again."),
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
        Rename environment
      </DialogTitle>
      <DialogContent>
        <Stack sx={{ pt: 1 }}>
          <TextField
            autoFocus
            fullWidth
            label="Environment name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            error={Boolean(error)}
            helperText={error || `1–${MAX_ENV_NAME} characters.`}
            inputProps={{ maxLength: MAX_ENV_NAME + 1 }}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose} disabled={rename.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={rename.isPending}
        >
          {rename.isPending ? "Saving…" : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

RenameEnvironmentDialog.propTypes = {
  open: PropTypes.bool,
  env: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  onClose: PropTypes.func,
};
