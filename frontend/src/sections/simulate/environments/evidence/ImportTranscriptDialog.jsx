import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  FormHelperText,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import {
  MAX_EVIDENCE_SOURCES,
  MAX_TRANSCRIPT_CHARS,
  TRANSCRIPT_NAME_CHARS,
  useCreateEvidenceImport,
} from "src/api/simulate-environments/evidenceImports";
import { EVIDENCE_COPY, errorText, guessFormat } from "./evidence.constants";

const FORMATS = Object.keys(EVIDENCE_COPY.formats);

function FormatSelect({ value, onChange }) {
  return (
    <TextField
      select
      size="small"
      label={EVIDENCE_COPY.formatLabel}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      sx={{ minWidth: 200 }}
    >
      {FORMATS.map((one) => (
        <MenuItem key={one} value={one}>
          {EVIDENCE_COPY.formats[one]}
        </MenuItem>
      ))}
    </TextField>
  );
}

FormatSelect.propTypes = { value: PropTypes.string, onChange: PropTypes.func };

// From an environment's Scenarios tab: a pasted transcript, or uploaded files
// (one case each), become an import, whose review then opens. Production is
// what the person says; unsaid, the transcripts are not production evidence.
export default function ImportTranscriptDialog({
  open,
  onClose,
  envId,
  onImported,
}) {
  const create = useCreateEvidenceImport();
  const fileInput = useRef(null);
  const [pasted, setPasted] = useState("");
  const [name, setName] = useState(EVIDENCE_COPY.pastedName);
  // Null until the person picks one: then it is guessed from the text.
  const [format, setFormat] = useState(null);
  const [files, setFiles] = useState([]);
  const [production, setProduction] = useState(false);
  const [fileProblem, setFileProblem] = useState("");

  const pastedName = name.trim() || EVIDENCE_COPY.pastedName;
  const pastedTooLong = !files.length && pasted.length > MAX_TRANSCRIPT_CHARS;
  const problem =
    fileProblem ||
    (pastedTooLong ? EVIDENCE_COPY.transcriptTooLong(pastedName) : "");
  let transcripts = files;
  if (!files.length) {
    transcripts = pasted.trim()
      ? [
          {
            name: pastedName,
            format: format ?? guessFormat("", pasted),
            text: pasted,
          },
        ]
      : [];
  }

  const pickFiles = async (event) => {
    const picked = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!picked.length) return;
    if (picked.length > MAX_EVIDENCE_SOURCES) {
      setFileProblem(EVIDENCE_COPY.transcriptsTooMany);
      return;
    }
    const read = await Promise.all(
      picked.map(async (file) => ({
        name: file.name.slice(0, TRANSCRIPT_NAME_CHARS),
        text: await file.text(),
      })),
    );
    const long = read.find((one) => one.text.length > MAX_TRANSCRIPT_CHARS);
    if (long) {
      setFileProblem(EVIDENCE_COPY.transcriptTooLong(long.name));
      return;
    }
    setFileProblem("");
    setFiles(
      read.map((one) => ({ ...one, format: guessFormat(one.name, one.text) })),
    );
  };

  const setFileFormat = (index, value) =>
    setFiles((current) =>
      current.map((one, at) =>
        at === index ? { ...one, format: value } : one,
      ),
    );

  const onImport = () =>
    create.mutate(
      { envId, sourceKind: "transcript", transcripts, production },
      {
        onSuccess: (made) => {
          onClose();
          onImported(made.id);
        },
      },
    );

  return (
    // A form: the design system's form dialog, md 560.
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={EVIDENCE_COPY.transcriptTitle}
      actionBtnTitle={EVIDENCE_COPY.create}
      isValid={Boolean(transcripts.length) && !problem}
      isLoading={create.isPending}
      onSubmit={onImport}
    >
      <Stack spacing={2}>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {EVIDENCE_COPY.transcriptBody}
        </Typography>
        {files.length ? (
          <Stack spacing={1} data-testid="transcript-files">
            {files.map((one, index) => (
              <Stack
                key={`${one.name}-${index}`}
                direction="row"
                spacing={1}
                alignItems="center"
              >
                <Typography
                  noWrap
                  sx={{ typography: "s1", flex: 1, minWidth: 0 }}
                >
                  {one.name}
                </Typography>
                <FormatSelect
                  value={one.format}
                  onChange={(value) => setFileFormat(index, value)}
                />
              </Stack>
            ))}
          </Stack>
        ) : (
          <>
            <TextField
              label={EVIDENCE_COPY.pasteLabel}
              multiline
              minRows={6}
              maxRows={14}
              fullWidth
              value={pasted}
              onChange={(event) => setPasted(event.target.value)}
            />
            <Stack direction="row" spacing={1}>
              <TextField
                size="small"
                label={EVIDENCE_COPY.nameLabel}
                value={name}
                onChange={(event) => setName(event.target.value)}
                inputProps={{ maxLength: TRANSCRIPT_NAME_CHARS }}
                sx={{ flex: 1 }}
              />
              <FormatSelect
                value={format ?? guessFormat("", pasted)}
                onChange={setFormat}
              />
            </Stack>
          </>
        )}
        <Box>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => {
              if (files.length) {
                setFiles([]);
                setFileProblem("");
              } else {
                fileInput.current?.click();
              }
            }}
          >
            {files.length ? EVIDENCE_COPY.usePaste : EVIDENCE_COPY.uploadFiles}
          </Button>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept=".json,.txt,.csv,application/json,text/plain,text/csv"
            hidden
            data-testid="transcript-file-input"
            onChange={pickFiles}
          />
        </Box>
        <Box>
          <FormControlLabel
            control={
              <Checkbox
                checked={production}
                onChange={(event) => setProduction(event.target.checked)}
              />
            }
            label={EVIDENCE_COPY.production}
          />
          <FormHelperText sx={{ mt: 0 }}>
            {EVIDENCE_COPY.productionHelp}
          </FormHelperText>
        </Box>
        {problem ? <Alert severity="error">{problem}</Alert> : null}
        {create.error ? (
          <Alert severity="error">{errorText(create.error)}</Alert>
        ) : null}
      </Stack>
    </ModalWrapper>
  );
}

ImportTranscriptDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  envId: PropTypes.string.isRequired,
  onImported: PropTypes.func.isRequired,
};
