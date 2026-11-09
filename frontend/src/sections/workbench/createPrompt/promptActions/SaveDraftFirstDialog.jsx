// IT-R2 G4 P2 · `Run on a dataset…` on a draft: an experiment runs a saved
// version, so the draft is committed first (`Save as V2 and run on a
// dataset?  [Save and continue]`) and New experiment opens on that version.
// The button itself is a frozen line (RunOnRowsButton, lane Rb); the guard
// listens on the action bar (`useRunOnDraftGuard().onClickCapture`), so the
// frozen lines stay untouched.
import React, { useEffect, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";

const RUN_ON_ROWS = '[data-testid="run-on-rows"]';

/**
 * @param {{promptId: string, version?: string, isDraft: boolean}} a
 * @returns {{onClickCapture: Function, dialog: React.ReactNode,
 *   savedVersion: string|null}}
 */
export function useRunOnDraftGuard({ promptId, version, isDraft }) {
  const queryClient = useQueryClient();
  const [ask, setAsk] = useState(null); // the clicked button (re-clicked)
  const [saving, setSaving] = useState(false);
  // options-truth L3 G8 P2: the version this dialog saved (V6), passed on
  // to `Run on a dataset…` so New experiment opens on it.
  const [savedVersion, setSavedVersion] = useState(null);
  const bypass = useRef(false);
  // A later draft (V7) is what runs next.
  useEffect(() => {
    if (savedVersion && version && version !== savedVersion)
      setSavedVersion(null);
  }, [version, savedVersion]);

  const onClickCapture = (e) => {
    const target = e.target?.closest?.(RUN_ON_ROWS);
    if (!target || bypass.current || !isDraft || !version) return;
    e.preventDefault();
    e.stopPropagation();
    setAsk(target);
  };

  const saveAndContinue = async () => {
    setSaving(true);
    try {
      await axios.post(endpoints.develop.runPrompt.commitSavePrompt(promptId), {
        message: "",
        set_default: false,
        is_draft: true,
        version_name: version,
      });
      setSavedVersion(version);
      enqueueSnackbar(`Saved ${version}`, { variant: "success" });
      // Re-click while the button still names the saved version, then
      // reload the header (the saved version, now committed).
      const button = ask?.querySelector?.("button") ?? ask;
      setAsk(null);
      bypass.current = true;
      button?.click?.();
      bypass.current = false;
      await Promise.all([
        queryClient.refetchQueries({
          queryKey: ["prompt-latest-version", promptId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["prompt-versions", promptId],
        }),
      ]);
    } catch {
      enqueueSnackbar(`Couldn't save ${version}. Try Commit.`, {
        variant: "error",
      });
    } finally {
      setSaving(false);
    }
  };

  const dialog = (
    <Dialog
      open={Boolean(ask)}
      onClose={saving ? undefined : () => setAsk(null)}
      maxWidth="xs"
      fullWidth
      // L5 G1: closing must not pull focus back to the Run button while
      // the dataset picker opens (its search takes the next keys).
      disableRestoreFocus
    >
      <DialogTitle>{`Save as ${version} and run on a dataset?`}</DialogTitle>
      <DialogContent>
        <Typography typography="s1" color="text.secondary">
          An experiment runs a saved version. This saves your draft as {version}{" "}
          (with its tools and variables), then opens New experiment on it.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button disabled={saving} onClick={() => setAsk(null)}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          loading={saving}
          onClick={saveAndContinue}
          data-testid="save-and-continue"
        >
          Save and continue
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );

  return { onClickCapture, dialog, savedVersion };
}
