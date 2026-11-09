/* eslint-disable react-refresh/only-export-components */
import React, { useRef, useState } from "react";
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
import { HubSection } from "./HubStates";
import {
  CHECKER_NOT_CONFIGURED,
  UploadsUnavailableText,
} from "./UploadsUnavailable";

export function uploadProblem(file) {
  if (!file) return null;
  const lower = file.name.toLowerCase();
  if (!UPLOAD_EXTENSIONS.some((ext) => lower.endsWith(ext)))
    return "Upload a .tar.gz or .zip of your entry folder.";
  if (file.size > MAX_UPLOAD_BYTES)
    return `This file is ${formatBytes(file.size)}; the limit is 2 MB.`;
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
  const navigate = useNavigate();
  const upload = useUploadSubmission();
  const problem = uploadProblem(file);
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
    <HubSection title="Upload a folder" testId="hub-upload-card">
      <Typography typography="s2" color="text.secondary">
        Pack your entry folder as a .tar.gz or .zip (at most 2 MB). It is
        checked automatically, then a Future AGI maintainer reviews it.
      </Typography>
      <Box
        role="button"
        tabIndex={0}
        aria-label="Choose a .tar.gz or .zip to upload"
        onClick={() => input.current?.click()}
        onKeyDown={(e) =>
          (e.key === "Enter" || e.key === " ") && input.current?.click()
        }
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
          border: "1.5px dashed",
          borderColor: dragging ? "primary.main" : "divider",
          borderRadius: 1,
          p: 3,
          textAlign: "center",
          cursor: "pointer",
          bgcolor: dragging ? "action.hover" : "background.neutral",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 0.75,
        }}
      >
        <Box sx={{ color: "text.secondary" }}>
          <HubIcon name="upload" size={22} />
        </Box>
        <Typography typography="s1" fontWeight={500}>
          {file ? file.name : "Drop a .tar.gz or .zip here, or choose a file"}
        </Typography>
        <Typography typography="s3" color="text.tertiary">
          {file ? formatBytes(file.size) : "At most 2 MB, 60 files"}
        </Typography>
        <input
          ref={input}
          type="file"
          hidden
          accept=".gz,.tgz,.zip,application/gzip,application/zip"
          data-testid="hub-upload-input"
          onChange={(e) => pick(e.target.files?.[0])}
        />
      </Box>
      {problem && <Alert severity="warning">{problem}</Alert>}
      <TextField
        select
        size="small"
        label="Publish as"
        value={publisher}
        onChange={(e) => setPublisher(e.target.value)}
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
        minRows={2}
        inputProps={{ maxLength: NOTES_MAX, "data-testid": "hub-upload-notes" }}
        helperText={`${notes.length} / ${NOTES_MAX}. Shown on the Versions tab.`}
      />
      {upload.isPending && (
        <LinearProgress
          variant="determinate"
          value={Math.round(progress * 100)}
        />
      )}
      {upload.isError && (
        <Alert severity="error" data-testid="hub-upload-error">
          {upload.error.code === CHECKER_NOT_CONFIGURED ? (
            <UploadsUnavailableText />
          ) : (
            <>
              {upload.error.message}
              {upload.error.fix ? ` ${upload.error.fix}` : ""}
            </>
          )}
        </Alert>
      )}
      <Box>
        <Button
          variant="contained"
          disabled={!file || Boolean(problem) || !publisher || upload.isPending}
          onClick={submit}
          data-testid="hub-upload-submit"
        >
          Submit for review
        </Button>
      </Box>
    </HubSection>
  );
}

UploadCard.propTypes = { publishers: PropTypes.array.isRequired };
