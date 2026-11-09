import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { notesByFile } from "../lib/report";

import HubIcon from "./HubIcon";

// A maintainer's file-and-line notes on a change request (SPEC §5.4), grouped
// by the file they refer to. One way: the submitter answers with a new upload.
export default function ChangeNotesList({ notes }) {
  const grouped = notesByFile(notes);
  const files = Object.keys(grouped).sort();
  if (!files.length) return null;
  return (
    <Box
      data-testid="hub-change-notes"
      sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}
    >
      {files.map((file) => (
        <Box
          key={file}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              px: 1.25,
              py: 0.75,
              bgcolor: "background.neutral",
            }}
          >
            <HubIcon name="file" size={13} />
            <Typography typography="s2" sx={{ fontFamily: "monospace" }}>
              {file}
            </Typography>
          </Box>
          <Box component="ul" sx={{ m: 0, p: 0, listStyle: "none" }}>
            {grouped[file]
              .slice()
              .sort((a, b) => (a.line || 0) - (b.line || 0))
              .map((note, index) => (
                <Box
                  component="li"
                  key={`${file}-${note.line}-${index}`}
                  data-testid="hub-change-note"
                  sx={{
                    display: "flex",
                    gap: 1.25,
                    px: 1.25,
                    py: 0.75,
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    typography="s3"
                    color="text.tertiary"
                    sx={{ fontFamily: "monospace", width: 56, flexShrink: 0 }}
                  >
                    {note.line ? `line ${note.line}` : "file"}
                  </Typography>
                  <Typography
                    typography="s2"
                    color="text.primary"
                    sx={{ overflowWrap: "anywhere" }}
                  >
                    {note.note}
                  </Typography>
                </Box>
              ))}
          </Box>
        </Box>
      ))}
    </Box>
  );
}

ChangeNotesList.propTypes = { notes: PropTypes.array };
