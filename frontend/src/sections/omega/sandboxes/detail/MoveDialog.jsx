import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import TextField from "@mui/material/TextField";

import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";

import { baseName, fileErrorMessage, moveProblem } from "../lib/files";
import {
  folderLookup,
  folderMissing,
  normalizeCwd,
  workdirLabel,
} from "../lib/runOptions";

/**
 * Move one file or folder into another folder of the sandbox. A folder is
 * picked from the ones the Files list knows (/workspace, the folders above
 * this one and the folders in it) or typed as a path inside /workspace,
 * checked as the Run tab checks its working folder. Before anything moves,
 * the folder's parent is listed to see that the folder is there, as Run
 * does; a look that fails leaves the move to the sandbox. The sandbox's own
 * refusal (something of that name already there) is shown here, and the
 * dialog stays open so another folder can be chosen.
 */
export default function MoveDialog({
  open,
  path,
  directory = false,
  folders = [],
  pending = false,
  error = null,
  listFolder,
  onSubmit,
  onClose,
}) {
  const [text, setText] = useState("");
  const [touched, setTouched] = useState(false);
  // The folder the last Move found missing, shown while it is still typed.
  const [missing, setMissing] = useState(null);
  const [checking, setChecking] = useState(false);
  // The dialog can't close during the look (ModalWrapper holds it while
  // loading), but its row can leave the list: then the answer moves nothing.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (open) {
      setText("");
      setTouched(false);
      setMissing(null);
    }
  }, [open]);

  const name = baseName(path);
  const target = normalizeCwd(text);
  const problem = moveProblem(text, { from: path, directory });
  let hint = null;
  if (problem && (touched || text.trim())) hint = problem;
  else if (!problem && missing !== null && missing === target)
    hint = `No folder ${workdirLabel(target)} in this sandbox. Make it first with New folder.`;
  // Never the folder it is in, nor (for a folder) itself or one inside it.
  const labels = folders.map(workdirLabel);
  const choices = labels.filter(
    (label) => !moveProblem(label, { from: path, directory }),
  );
  const busy = pending || checking;

  const submit = async () => {
    setTouched(true);
    if (problem || busy) return;
    const lookup = folderLookup(target);
    if (lookup && listFolder) {
      setChecking(true);
      const listing = await listFolder(lookup.parent).catch(() => null);
      if (!mounted.current) return;
      setChecking(false);
      if (folderMissing(target, listing)) {
        setMissing(target);
        return;
      }
    }
    setMissing(null);
    onSubmit(target);
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={`Move ${name}`}
      subTitle="Choose a folder inside /workspace, or type its path."
      actionBtnTitle="Move"
      isValid={!problem}
      isLoading={busy}
      onSubmit={() => void submit()}
      modalWidth="480px"
      actionBtnProps={{ "data-testid": "file-move-submit" }}
    >
      <Box
        component="form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
      >
        <Autocomplete
          freeSolo
          forcePopupIcon
          options={choices}
          inputValue={text}
          onInputChange={(_, value) => setText(value)}
          readOnly={busy}
          renderInput={(params) => (
            <TextField
              {...params}
              autoFocus
              size="small"
              label="Folder"
              placeholder="/workspace/src"
              error={Boolean(hint)}
              helperText={
                hint || "The folder must exist; New folder makes one."
              }
              inputProps={{
                ...params.inputProps,
                "data-testid": "file-move-folder",
                spellCheck: false,
                autoCapitalize: "off",
              }}
              sx={{ "& input": { fontFamily: "monospace" } }}
            />
          )}
        />
        {error && (
          <Alert severity="error" data-testid="file-move-error">
            {fileErrorMessage(error)}
          </Alert>
        )}
      </Box>
    </ModalWrapper>
  );
}

MoveDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  // The file or folder to move, relative to /workspace.
  path: PropTypes.string.isRequired,
  directory: PropTypes.bool,
  // Folders to offer, relative to /workspace ("" is /workspace itself).
  folders: PropTypes.arrayOf(PropTypes.string),
  pending: PropTypes.bool,
  error: PropTypes.object,
  // Reads a folder's list (listFiles), to see that the chosen folder is there.
  listFolder: PropTypes.func,
  // Called with the folder, relative to /workspace.
  onSubmit: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};
