import React, { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { CopyButton } from "../settings/ui";
import { GATEWAY_MONO } from "../utils/fonts";
import { useValidateCustomProperties } from "./hooks/useCustomProperties";
import { sampleValue } from "./reservedNames";

function MetadataSample({ onClose, properties }) {
  const titleId = useId();
  // Initialize once per opening, not on a background schema-list refresh.
  // These are examples, never stored defaults or a claim that rules passed.
  const [draft, setDraft] = useState(() =>
    JSON.stringify(
      Object.fromEntries(
        properties.map((property) => [property.name, sampleValue(property)]),
      ),
      null,
      2,
    ),
  );
  const [inputError, setInputError] = useState("");
  const [requestFailed, setRequestFailed] = useState(false);
  const [result, setResult] = useState(null);
  const inFlight = useRef(false);
  const { mutate, isPending } = useValidateCustomProperties();

  const changeDraft = (event) => {
    setDraft(event.target.value);
    setInputError("");
    setRequestFailed(false);
    setResult(null);
  };

  const validate = () => {
    if (inFlight.current) return;
    setInputError("");
    setRequestFailed(false);
    setResult(null);

    let propertiesToValidate;
    try {
      propertiesToValidate = JSON.parse(draft);
    } catch {
      setInputError("Enter valid JSON before validating.");
      return;
    }
    if (
      propertiesToValidate === null ||
      Array.isArray(propertiesToValidate) ||
      typeof propertiesToValidate !== "object"
    ) {
      setInputError("Metadata must be a JSON object, not null, an array or a scalar.");
      return;
    }

    inFlight.current = true;
    mutate(
      { properties: propertiesToValidate },
      {
        onSuccess: (response) => {
          inFlight.current = false;
          // A missing result is not a pass. The existing API returns a
          // boolean valid flag and an array of error strings.
          if (
            typeof response?.valid !== "boolean" ||
            !Array.isArray(response.errors) ||
            !response.errors.every((error) => typeof error === "string")
          ) {
            setRequestFailed(true);
            return;
          }
          setResult(response);
        },
        onError: () => {
          inFlight.current = false;
          setRequestFailed(true);
        },
      },
    );
  };

  return (
    <Dialog
      open
      onClose={onClose}
      aria-labelledby={titleId}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle id={titleId}>Test metadata</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <Box>
            <Typography typography="s1">
              Check a metadata sample against saved schemas only. Unknown properties
              are allowed by the current API.
            </Typography>
            <Typography typography="s2" color="text.secondary" sx={{ mt: 0.5 }}>
              Passing validation does not prove request ingestion or gateway enforcement.
              No inference request is sent and no schemas are changed.
            </Typography>
          </Box>
          <Box>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              spacing={1}
              sx={{ mb: 1 }}
            >
              <Typography typography="s2" color="text.secondary">
                Edit these example values to match your metadata.
              </Typography>
              <CopyButton text={draft} label="Copy sample" />
            </Stack>
            <TextField
              label="Metadata JSON"
              value={draft}
              onChange={changeDraft}
              multiline
              minRows={8}
              maxRows={16}
              fullWidth
              disabled={isPending}
              error={Boolean(inputError)}
              helperText={
                inputError ||
                "Enter the metadata object itself, without a properties or metadata wrapper."
              }
              InputProps={{ sx: { typography: "s2", fontFamily: GATEWAY_MONO } }}
              inputProps={{ spellCheck: false }}
            />
          </Box>
          {requestFailed && (
            <Alert severity="error" variant="outlined">
              Could not validate metadata. Your sample is unchanged. Choose Retry to try again.
            </Alert>
          )}
          {result && (
            <Alert
              severity={result.valid ? "success" : "warning"}
              variant="outlined"
              role="status"
            >
              <Typography typography="s1">
                {result.valid
                  ? "Passed validation — not enforced by gateway."
                  : "Failed validation"}
              </Typography>
              {result.errors.length > 0 && (
                <Box
                  component="ul"
                  aria-label="Validation errors"
                  sx={{ m: 0, mt: 1, pl: 2.5 }}
                >
                  {result.errors.map((error, index) => (
                    <Typography
                      component="li"
                      typography="s2"
                      key={`${index}-${error}`}
                      sx={{ overflowWrap: "anywhere" }}
                    >
                      {error}
                    </Typography>
                  ))}
                </Box>
              )}
            </Alert>
          )}
          <Typography typography="s2" color="text.secondary">
            Closing discards this sample and its result. Reopening starts with examples
            from the saved schemas shown on this page.
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={validate}
          disabled={isPending}
        >
          {isPending ? "Validating…" : requestFailed ? "Retry" : "Validate"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

MetadataSample.propTypes = {
  onClose: PropTypes.func.isRequired,
  properties: PropTypes.array.isRequired,
};

// Unmount the draft and mutation observer on close. A response from an old
// opening must not appear as the result for a new sample, and no draft is stored.
export default function TestMetadataDialog({ open, onClose, properties = [] }) {
  return open ? <MetadataSample onClose={onClose} properties={properties} /> : null;
}

TestMetadataDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  properties: PropTypes.array,
};
