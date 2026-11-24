import PropTypes from "prop-types";
import { useState } from "react";
import { Button, Popover, Stack, TextField, Typography } from "@mui/material";
import { useSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import { ARCHIVE_REASON_MAX } from "src/api/simulate-environments/scenarios";
import { useArchiveScenario } from "src/api/simulate-environments/scenariosHooks";
import { LIFECYCLE_COPY } from "./scenarios.constants";

const COPY = LIFECYCLE_COPY.archive;

// The request layer hands back the server's error body itself.
const errorText = (error) => COPY.errors[error?.error] ?? error?.message;

// Archive (with an optional note) or Restore one scenario. `onChanged` gets the scenario's new lifecycle.
export default function ArchiveScenarioButton({ envId, row, onChanged }) {
  const { enqueueSnackbar } = useSnackbar();
  const [anchor, setAnchor] = useState(null);
  const [note, setNote] = useState("");
  const mutation = useArchiveScenario(envId);
  const archived = row?.lifecycle?.state === "archived";
  // A scenario only the unsaved changes add can't be archived; it is removed instead.
  if (!row?.lifecycle || !row.scenarioKey || row.draftChange === "added")
    return null;

  const run = async (restore) => {
    try {
      const result = await mutation.mutateAsync({
        key: row.scenarioKey,
        reason: note,
        restore,
      });
      setAnchor(null);
      setNote("");
      onChanged?.(result.lifecycle);
      const said = !restore
        ? COPY.archived
        : result.live
          ? COPY.restoredLive
          : COPY.restoredDraft;
      enqueueSnackbar(said, { variant: "success" });
    } catch (error) {
      enqueueSnackbar(errorText(error), { variant: "error" });
    }
  };

  if (archived) {
    return (
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={mutation.isPending}
        onClick={() => run(true)}
        startIcon={<Iconify icon="solar:restart-linear" width={14} />}
        sx={{ flexShrink: 0 }}
      >
        {COPY.restore}
      </Button>
    );
  }

  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:archive-linear" width={14} />}
        sx={{ flexShrink: 0 }}
      >
        {COPY.action}
      </Button>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <Stack spacing={1.5} sx={{ p: 2, width: 320 }}>
          <Typography sx={{ typography: "m3" }}>{COPY.title}</Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {COPY.body}
          </Typography>
          <TextField
            size="small"
            label={COPY.note}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            inputProps={{ maxLength: ARCHIVE_REASON_MAX }}
          />
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button
              size="small"
              color="inherit"
              onClick={() => setAnchor(null)}
            >
              {COPY.cancel}
            </Button>
            <Button
              size="small"
              variant="contained"
              disabled={mutation.isPending}
              onClick={() => run(false)}
            >
              {COPY.action}
            </Button>
          </Stack>
        </Stack>
      </Popover>
    </>
  );
}

ArchiveScenarioButton.propTypes = {
  envId: PropTypes.string,
  row: PropTypes.object,
  onChanged: PropTypes.func,
};
