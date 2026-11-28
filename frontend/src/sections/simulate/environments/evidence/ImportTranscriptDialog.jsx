import PropTypes from "prop-types";
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Divider,
  FormControlLabel,
  FormHelperText,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { Upload } from "src/components/upload";
import {
  MAX_EVIDENCE_SOURCES,
  MAX_TRANSCRIPT_CHARS,
  TRANSCRIPT_NAME_CHARS,
  useCreateEvidenceImport,
} from "src/api/simulate-environments/evidenceImports";
import { EVIDENCE_COPY, errorText, guessFormat } from "./evidence.constants";

const FORMATS = Object.keys(EVIDENCE_COPY.formats);
// What the drop zone and its file picker take.
const ACCEPT = {
  "application/json": [".json"],
  "text/plain": [".txt"],
  "text/csv": [".csv"],
};

// `fileName` makes it a file row's compact select: no label above it (the
// row says which file), and its name for screen readers says which file.
function FormatSelect({ value, onChange, fileName }) {
  return (
    <TextField
      select
      size="small"
      label={fileName ? undefined : EVIDENCE_COPY.formatLabel}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      SelectProps={
        fileName
          ? {
              inputProps: {
                "aria-label": `${EVIDENCE_COPY.formatLabel} of ${fileName}`,
              },
            }
          : undefined
      }
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

FormatSelect.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  fileName: PropTypes.string,
};

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

  // Dropped or browsed files join the ones already added, up to the limit.
  // A file of the wrong type is refused on its own: the others in the same
  // drop are still added.
  const addFiles = async (picked, rejected = []) => {
    const refused = rejected.length
      ? EVIDENCE_COPY.wrongType(rejected[0].file.name)
      : "";
    if (!picked.length) {
      if (refused) setFileProblem(refused);
      return;
    }
    if (files.length + picked.length > MAX_EVIDENCE_SOURCES) {
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
    setFileProblem(refused);
    setFiles((current) => [
      ...current,
      ...read.map((one) => ({
        ...one,
        format: guessFormat(one.name, one.text),
      })),
    ]);
  };

  const removeFile = (index) => {
    setFiles((current) => current.filter((_, at) => at !== index));
    setFileProblem("");
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
                sx={{
                  pl: 1.25,
                  pr: 0.5,
                  py: 0.75,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                <Iconify
                  icon="solar:document-text-linear"
                  width={16}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Typography
                  noWrap
                  sx={{ typography: "s1", flex: 1, minWidth: 0 }}
                >
                  {one.name}
                </Typography>
                <FormatSelect
                  value={one.format}
                  onChange={(value) => setFileFormat(index, value)}
                  fileName={one.name}
                />
                <IconButton
                  size="small"
                  aria-label={EVIDENCE_COPY.removeFile(one.name)}
                  onClick={() => removeFile(index)}
                >
                  <Iconify icon="solar:close-circle-linear" width={16} />
                </IconButton>
              </Stack>
            ))}
            <Box>
              <Button
                size="small"
                color="inherit"
                onClick={() => {
                  setFiles([]);
                  setFileProblem("");
                }}
              >
                {EVIDENCE_COPY.usePaste}
              </Button>
            </Box>
          </Stack>
        ) : (
          <>
            <TextField
              label={EVIDENCE_COPY.pasteLabel}
              multiline
              minRows={5}
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
            <Divider>
              <Typography sx={{ typography: "s2", color: "text.disabled" }}>
                {EVIDENCE_COPY.or}
              </Typography>
            </Divider>
          </>
        )}
        {files.length < MAX_EVIDENCE_SOURCES && (
          <Upload
            multiple
            hidePreview
            showIllustration={false}
            showDropRejection={false}
            accept={ACCEPT}
            onDrop={(accepted, rejected) => addFiles(accepted, rejected)}
            uploadIcon={
              <Iconify
                icon="solar:upload-minimalistic-linear"
                width={24}
                sx={{ color: "primary.main" }}
              />
            }
            heading={EVIDENCE_COPY.dropHeading}
            description={[EVIDENCE_COPY.dropFormats, EVIDENCE_COPY.dropLimits]}
            actionButton={
              <Button size="small" variant="outlined" color="inherit">
                {EVIDENCE_COPY.browseFiles}
              </Button>
            }
            sx={{ py: files.length ? 1.5 : 2.5 }}
          />
        )}
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {EVIDENCE_COPY.transcriptPrivacy}
        </Typography>
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
