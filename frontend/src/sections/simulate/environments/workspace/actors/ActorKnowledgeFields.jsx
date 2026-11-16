import PropTypes from "prop-types";
import { useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  TextField,
  Typography,
} from "@mui/material";
import { refusalText } from "../evals/refusalText";
import { ACTORS_COPY } from "./actors.constants";

const F = ACTORS_COPY.worldActors.form;
const recordLabel = (one) => `${one.store}.${one.collection}`;

/**
 * What an actor can read beyond its facts: the environment's files (with a document uploaded on the spot) and the
 * world's record collections, each picked from what the cast offers. A server problem on either shows under it.
 */
export default function ActorKnowledgeFields({
  files,
  records,
  sources,
  upload,
  problems,
  onChange,
}) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  return (
    <>
      <Autocomplete
        multiple
        size="small"
        options={sources.files}
        value={files}
        onChange={(_, next) => onChange("files", next)}
        renderInput={(params) => (
          <TextField
            {...params}
            label={F.files}
            error={Boolean(problems.files)}
            helperText={
              problems.files || (sources.files.length ? F.filesHelp : F.noFiles)
            }
          />
        )}
      />
      {upload && (
        <Box>
          <Button
            size="small"
            component="label"
            disabled={uploading}
            sx={{ alignSelf: "flex-start" }}
          >
            {uploading ? F.uploading : F.upload}
            <input
              hidden
              type="file"
              accept=".md,.txt,.json,.csv"
              onChange={async (event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (!file) return;
                setUploading(true);
                setUploadError("");
                try {
                  const path = await upload(file);
                  onChange("files", [...new Set([...files, path])]);
                } catch (failure) {
                  setUploadError(refusalText(failure, F.uploadFailed));
                } finally {
                  setUploading(false);
                }
              }}
            />
          </Button>
          <Typography
            sx={{
              typography: "s3",
              color: uploadError ? "error.main" : "text.secondary",
            }}
          >
            {uploadError || F.uploadHelp}
          </Typography>
        </Box>
      )}
      <Autocomplete
        multiple
        size="small"
        options={sources.records}
        value={records}
        onChange={(_, next) => onChange("records", next)}
        getOptionLabel={recordLabel}
        isOptionEqualToValue={(one, other) =>
          recordLabel(one) === recordLabel(other)
        }
        renderInput={(params) => (
          <TextField
            {...params}
            label={F.recordsRead}
            error={Boolean(problems.records)}
            helperText={
              problems.records ||
              (sources.records.length ? F.recordsHelp : F.noRecords)
            }
          />
        )}
      />
    </>
  );
}

const RECORD = PropTypes.shape({
  store: PropTypes.string,
  collection: PropTypes.string,
});

ActorKnowledgeFields.propTypes = {
  files: PropTypes.arrayOf(PropTypes.string).isRequired,
  records: PropTypes.arrayOf(RECORD).isRequired,
  // What the cast offers to read.
  sources: PropTypes.shape({
    files: PropTypes.arrayOf(PropTypes.string).isRequired,
    records: PropTypes.arrayOf(RECORD).isRequired,
  }).isRequired,
  // Uploads a document into the draft and resolves to where it was stored; absent when the environment is locked.
  upload: PropTypes.func,
  // The server's problem on `files` or `records`, by field.
  problems: PropTypes.objectOf(PropTypes.string).isRequired,
  // (field, next value)
  onChange: PropTypes.func.isRequired,
};
