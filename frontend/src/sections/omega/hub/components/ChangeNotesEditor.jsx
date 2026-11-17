import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { CHANGE_NOTES_MAX, NOTES_MAX } from "../constants";

import HubIcon from "./HubIcon";

// File-and-line notes for a change request (SPEC §5.4): at most 30, each on a
// file from the submission's own tree. The submitter sees them beside the
// file and cannot write back; they answer with a new upload.
export default function ChangeNotesEditor({ notes, onChange, files }) {
  const paths = (files || []).map((f) => f.path).sort();
  const [file, setFile] = useState(paths[0] || "");
  const [line, setLine] = useState("");
  const [note, setNote] = useState("");
  const full = notes.length >= CHANGE_NOTES_MAX;
  const add = () => {
    const parsed = Number.parseInt(line, 10);
    onChange([...notes, { file, line: Number.isFinite(parsed) && parsed > 0 ? parsed : null, note: note.trim() }]);
    setNote("");
    setLine("");
  };
  return (
    <Box data-testid="hub-change-notes-editor" sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {notes.map((item, index) => (
        <Box
          key={`${item.file}-${item.line}-${index}`}
          data-testid="hub-change-note-draft"
          sx={{ display: "flex", gap: 1, alignItems: "flex-start", p: 1, borderRadius: 0.75, bgcolor: "background.neutral" }}
        >
          <Typography typography="s2" sx={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>
            <Box component="span" sx={{ fontFamily: "monospace", fontWeight: 600 }}>
              {`${item.file}${item.line ? `:${item.line}` : ""}`}
            </Box>
            {` ${item.note}`}
          </Typography>
          <IconButton size="small" aria-label="Remove this note" onClick={() => onChange(notes.filter((_, i) => i !== index))}>
            <HubIcon name="close" size={13} />
          </IconButton>
        </Box>
      ))}
      {full ? (
        <Typography typography="s3" color="text.secondary" data-testid="hub-change-notes-full">
          {`That is the limit of ${CHANGE_NOTES_MAX} notes.`}
        </Typography>
      ) : (
        <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr 80px", sm: "minmax(0, 1fr) 80px" } }}>
          <TextField
            select
            size="small"
            label="File"
            value={file}
            onChange={(e) => setFile(e.target.value)}
            inputProps={{ "data-testid": "hub-change-note-file" }}
          >
            {paths.map((path) => (
              <MenuItem key={path} value={path}>
                {path}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            label="Line"
            value={line}
            onChange={(e) => setLine(e.target.value.replace(/[^0-9]/g, ""))}
            inputProps={{ inputMode: "numeric", "data-testid": "hub-change-note-line" }}
          />
          <TextField
            size="small"
            label="Note on this file"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            multiline
            inputProps={{ maxLength: NOTES_MAX, "data-testid": "hub-change-note-text" }}
            sx={{ gridColumn: "1 / -1" }}
          />
          <Box sx={{ gridColumn: "1 / -1" }}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<HubIcon name="plus" size={14} />}
              disabled={!file || !note.trim()}
              onClick={add}
              data-testid="hub-change-note-add"
            >
              Add note
            </Button>
          </Box>
        </Box>
      )}
    </Box>
  );
}

ChangeNotesEditor.propTypes = {
  notes: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  files: PropTypes.array,
};
