/* eslint-disable react-refresh/only-export-components */
import React, { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import {
  MAX_UPLOAD_BYTES,
  NOTES_MAX,
  UPLOAD_EXTENSIONS,
  hubPaths,
} from "../constants";
import { useUploadSubmission } from "../api/queries";
import { formatBytes } from "../lib/format";

import HubIcon from "./HubIcon";
import HubPanel from "./HubPanel";
import { problemText } from "./PublisherSettings";
import {
  CHECKER_NOT_CONFIGURED,
  UploadsUnavailableText,
} from "./UploadsUnavailable";

// A refused upload says why in the server's words; a gateway error with no
// reason, or a dropped connection, says this instead.
const NOT_UPLOADED =
  "The file was not uploaded. The Hub registry did not answer; try again in a moment.";

export function uploadProblem(file) {
  if (!file) return null;
  const lower = file.name.toLowerCase();
  if (!UPLOAD_EXTENSIONS.some((ext) => lower.endsWith(ext)))
    return "Upload a .tar.gz or .zip of your entry folder.";
  if (file.size > MAX_UPLOAD_BYTES)
    return `This file is ${formatBytes(file.size)}; the limit is 2 MB.`;
  return null;
}

// Why "Submit for review" is off, said beside the button and read with it.
export function submitBlocker({ file, problem, publisher }) {
  if (!file) return "Choose a file first.";
  if (problem) return "Choose a .tar.gz or .zip of at most 2 MB.";
  if (!publisher) return "Choose who to publish as.";
  return null;
}

// "Upload a folder" (SPEC §7.5): a .tar.gz or .zip of at most 2 MiB, a
// publisher and optional version notes. The checker reads the manifest, so
// nothing is parsed here.
export default function UploadCard({ publishers }) {
  const [file, setFile] = useState(null);
  const [publisher, setPublisher] = useState(publishers[0]?.handle || "");
  const [notes, setNotes] = useState("");
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(0);
  const input = useRef(null);
  const id = useId();
  const navigate = useNavigate();
  const upload = useUploadSubmission();
  const problem = uploadProblem(file);
  const blocker = submitBlocker({ file, problem, publisher });
  const reasonId = `${id}-reason`;
  const choose = () => input.current?.click();
  const pick = (picked) => {
    upload.reset();
    setFile(picked || null);
  };
  const submit = async () => {
    try {
      const result = await upload.mutateAsync({
        file,
        publisher,
        notes: notes.trim(),
        onProgress: setProgress,
      });
      navigate(hubPaths.submission(result.id));
    } catch {
      // The card shows upload.error with the server's fix.
    }
  };
  return (
    <HubPanel
      title="Upload a folder"
      testId="hub-upload-card"
      description="Pack your entry folder as a .tar.gz or .zip (at most 2 MB). It is checked automatically, then a Future AGI maintainer reviews it."
    >
      <Box
        role="button"
        tabIndex={0}
        aria-label={
          file
            ? `${file.name} is chosen. Choose another file`
            : "Choose a .tar.gz or .zip to upload"
        }
        onClick={choose}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            choose();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pick(e.dataTransfer.files?.[0]);
        }}
        data-testid="hub-drop-zone"
        sx={{
          border: "1px dashed",
          borderColor: dragging ? "primary.main" : "divider",
          borderRadius: 1,
          px: 2,
          py: 3,
          textAlign: "center",
          cursor: "pointer",
          bgcolor: dragging ? "action.hover" : "background.neutral",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 0.75,
          "&:hover": { borderColor: "text.secondary" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 2,
          },
        }}
      >
        <Box sx={{ color: "text.secondary", display: "flex" }}>
          <HubIcon name={file ? "file" : "upload"} size={20} />
        </Box>
        <Typography
          typography="s1"
          fontWeight={500}
          color="text.primary"
          sx={{ overflowWrap: "anywhere" }}
        >
          {file ? file.name : "Drop a .tar.gz or .zip here, or choose a file"}
        </Typography>
        <Typography typography="s2" color="text.tertiary">
          {file
            ? `${formatBytes(file.size)}. Choose or drop another file to replace it.`
            : "At most 2 MB, 60 files"}
        </Typography>
      </Box>
      <input
        ref={input}
        type="file"
        hidden
        accept=".gz,.tgz,.zip,application/gzip,application/zip"
        data-testid="hub-upload-input"
        onChange={(e) => pick(e.target.files?.[0])}
      />
      {problem && <Alert severity="warning">{problem}</Alert>}
      <TextField
        select
        size="small"
        label="Publish as"
        value={publisher}
        onChange={(e) => setPublisher(e.target.value)}
        helperText="Entries are published as publisher/name."
        inputProps={{ "data-testid": "hub-upload-publisher" }}
      >
        {publishers.map((p) => (
          <MenuItem key={p.handle} value={p.handle}>
            {`${p.displayName} (@${p.handle})`}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        size="small"
        label="Version notes (optional)"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        multiline
        minRows={3}
        inputProps={{ maxLength: NOTES_MAX, "data-testid": "hub-upload-notes" }}
        helperText={`Shown on the Versions tab. ${notes.length} / ${NOTES_MAX}`}
      />
      {upload.isPending && (
        <LinearProgress
          variant="determinate"
          value={Math.round(progress * 100)}
          aria-label="Upload progress"
        />
      )}
      {upload.isError && (
        <Alert severity="error" data-testid="hub-upload-error">
          {upload.error.code === CHECKER_NOT_CONFIGURED ? (
            <UploadsUnavailableText />
          ) : (
            problemText(upload.error, NOT_UPLOADED)
          )}
        </Alert>
      )}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 1.5,
        }}
      >
        <Button
          variant="contained"
          disabled={Boolean(blocker) || upload.isPending}
          onClick={submit}
          aria-describedby={blocker ? reasonId : undefined}
          data-testid="hub-upload-submit"
        >
          Submit for review
        </Button>
        {blocker && (
          <Typography id={reasonId} typography="s1" color="text.secondary">
            {blocker}
          </Typography>
        )}
      </Box>
    </HubPanel>
  );
}

UploadCard.propTypes = { publishers: PropTypes.array.isRequired };
