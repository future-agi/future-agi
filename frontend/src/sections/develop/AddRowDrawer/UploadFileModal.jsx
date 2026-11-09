import {
  Box,
  Button,
  Chip,
  FormControlLabel,
  Radio,
  RadioGroup,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  MenuItem,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useController, useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import UploadedFileIllustration from "./UploadFileIllustration";
import { zodResolver } from "@hookform/resolvers/zod";
import { UploadFileValidationSchema } from "./validation";
import { RHFUpload } from "src/components/hook-form";
import axios from "src/utils/axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import CustomTooltip from "src/components/tooltip";
import { IMPORTS_PATH } from "src/sections/common/AddData/mappingModel";
import { useChangeSetStore } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { latestOf, useDatasetVersions } from "src/api/develop/dataset-versions";
import { revealNewRows } from "src/sections/develop-detail/DataTab/DataGrid/newRowsReveal";
import ImportFileExtras from "../AddDatasetDrawer/ImportFileExtras";
import {
  filePreviewKey,
  addBlockReason,
  addButtonLabel,
  archivedMatchText,
  fileImportBody,
  importNotes,
  importSummaryText,
  invalidItemsText,
  toPendingOps,
  topIssues,
  transformNotes,
} from "./fileImport";
import { LoadingButton } from "@mui/lab";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import UploadCard from "../AddDatasetDrawer/UploadCard";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { gridWritesClassic } from "../realBackendCaps";
import ClassicRowUploadModal from "./ClassicRowUploadModal";
import {
  ACCEPTED,
  extOf,
  fileProblem,
  guessRole,
  parseImport,
  ROLE_HINTS,
  ROLE_OPTIONS,
  SAMPLE_CSV,
} from "../AddDatasetDrawer/importFile";

/**
 * Role guessed from a header (W3-1: the one role list of both upload
 * dialogs; `split` → Split, `tags` → Tags, `id` → Ignore until you pick ID).
 */
export const roleForHeader = (column) => guessRole(String(column ?? "").trim());

const defaultValues = {
  name: "",
  file: null,
  modelType: "",
};

const StagedUploadFileModal = ({
  open,
  onClose,
  refreshGrid,
  datasetId,
  closeDrawer,
}) => {
  const { control, handleSubmit, reset, setValue } = useForm({
    defaultValues,
    resolver: zodResolver(UploadFileValidationSchema),
  });

  const queryClient = useQueryClient();
  const [parsed, setParsed] = useState(null);
  const [roles, setRoles] = useState({});
  const [detailsAnchor, setDetailsAnchor] = useState(null);
  const [source, setSource] = useState(null); // {name, text}
  const [flatten, setFlatten] = useState(false);
  const [parseJson, setParseJson] = useState(false);
  const [notInFile, setNotInFile] = useState("keep");
  const [archivedMatches, setArchivedMatches] = useState("keep");

  // W3-1: the ONE parser reads the file again when a toggle changes.
  useEffect(() => {
    if (!source) return;
    const result = parseImport(source.text, extOf(source.name), {
      flatten,
      parseJson,
    });
    setParsed(result);
    setRoles((cur) =>
      Object.fromEntries(
        result.columns.map((column) => [
          column,
          cur[column] ?? roleForHeader(column),
        ]),
      ),
    );
  }, [source, flatten, parseJson]);

  const body = useMemo(
    () =>
      source && datasetId
        ? fileImportBody({
            datasetId,
            fileName: source.name,
            text: source.text,
            roles,
            flatten,
            parseJson,
            notInFile,
            archivedMatches,
          })
        : null,
    [source, datasetId, roles, flatten, parseJson, notInFile, archivedMatches],
  );
  const blockedLocally = Boolean(parsed?.problem) || !parsed?.total;
  // v2.1 g8 (rows:upload-file): the preview is per dataset version (and
  // per pending change count), so uploading the same file again after a
  // save recomputes it instead of showing the earlier answer.
  const { data: versions } = useDatasetVersions(datasetId, {
    enabled: Boolean(open && datasetId),
  });
  const versionKey = latestOf(versions)?.id ?? null;
  const pendingCount = useChangeSetStore(
    (s) => s.sets?.[datasetId]?.ops?.length ?? 0,
  );
  // IM1 preview: what the import will do (new, updated by ID, unchanged,
  // rows the schema rejects, new columns and splits). Writes nothing.
  const preview = useQuery({
    queryKey: filePreviewKey({
      datasetId,
      versionKey,
      pendingCount,
      source,
      roles,
      flatten,
      parseJson,
      notInFile,
      archivedMatches,
    }),
    queryFn: () =>
      axios
        .post(IMPORTS_PATH, { op: "preview", ...body })
        .then((r) => r?.data?.result ?? null),
    enabled: Boolean(open && body && !blockedLocally),
    staleTime: 0,
    retry: false,
  });
  const summary = blockedLocally ? null : preview.data?.summary ?? null;
  const idColumn = preview.data?.id_column ?? null;

  const resetAll = () => {
    reset();
    setParsed(null);
    setRoles({});
    setSource(null);
    setFlatten(false);
    setParseJson(false);
    setNotInFile("keep");
    setArchivedMatches("keep");
  };

  const { mutate: uploadDataset, isPending } = useMutation({
    mutationFn: () =>
      axios
        .post(IMPORTS_PATH, { op: "commit", ...body })
        .then((r) => r?.data?.result ?? {}),
    onSuccess: (res) => {
      trackEvent(Events.addRowsSuccess, {
        [PropertyName.method]: "add using Upload a file",
      });
      const ops = toPendingOps(res?.operations ?? [], {
        stamp: `${Date.now().toString(36)}-`,
        hint: res?.description_hint,
      });
      const cs = useChangeSetStore.getState();
      ops.forEach((op) => cs.addOp(datasetId, op));
      revealNewRows(
        datasetId,
        ops.filter((o) => o.op === "add_row").map((o) => o.temp_id),
      );
      const added = ops.filter((o) => o.op === "add_row").length;
      const updated = res?.rows_updated ?? 0;
      const restored = res?.rows_restored ?? 0;
      enqueueSnackbar(
        `${added} ${added === 1 ? "row" : "rows"}${updated ? ` and ${updated} ${updated === 1 ? "update" : "updates"}` : ""}${restored ? ` and ${restored} ${restored === 1 ? "restore" : "restores"}` : ""} added to pending changes`,
        { variant: "success" },
      );
      if (res?.new_columns?.length) {
        queryClient.invalidateQueries({
          predicate: (q) => JSON.stringify(q.queryKey).includes(datasetId),
        });
        refreshGrid?.(null, true);
      }
      queryClient.removeQueries({
        queryKey: filePreviewKey({ datasetId }).slice(0, 2),
      });
      onClose();
      resetAll();
      closeDrawer?.();
    },
    onError: (e) =>
      enqueueSnackbar(
        typeof e?.result === "string"
          ? e.result
          : e?.message ?? "Couldn't read the file",
        { variant: "error" },
      ),
  });

  const { field } = useController({
    name: "file",
    control,
  });

  const handleFileChange = async (acceptedFiles) => {
    const file = acceptedFiles[0];
    if (!file) return;
    field.onChange({
      file,
      preview: <UploadedFileIllustration width={100} height={100} />,
    });
    const ext = extOf(file.name);
    const tooBig = fileProblem(file);
    if (tooBig && ACCEPTED.includes(ext)) {
      setSource(null);
      setParsed({
        columns: [],
        rows: [],
        errors: [],
        total: 0,
        problem: tooBig,
      });
      return;
    }
    if (!ACCEPTED.includes(ext)) {
      setParsed({
        columns: [],
        rows: [],
        errors: [{ line: 1, message: "Unsupported file format" }],
        total: 0,
      });
      return;
    }
    try {
      const text = await file.text();
      setRoles({});
      setSource({ name: file.name, text });
    } catch (error) {
      setParsed({
        columns: [],
        rows: [],
        errors: [
          { line: 1, message: error?.message ?? "Couldn't read this file" },
        ],
        total: 0,
      });
    }
  };

  const onSubmit = () => uploadDataset();

  const onCloseClick = () => {
    onClose();
  };

  const onDelete = () => {
    setValue("file", null);
    setParsed(null);
    setRoles({});
    setSource(null);
  };
  const blockReason =
    parsed?.problem ??
    (parsed && !parsed.total ? "The file has no rows to add" : null) ??
    addBlockReason(summary, notInFile, archivedMatches);
  const invalidHref =
    summary?.invalid && preview.data?.items
      ? `data:application/x-ndjson;charset=utf-8,${encodeURIComponent(invalidItemsText(preview.data.items))}`
      : null;

  return (
    <Dialog open={open} onClose={onCloseClick} maxWidth="md" fullWidth>
      <Box padding={2}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <Typography
                variant="m3"
                fontWeight={"fontWeightBold"}
                color="text.primary"
              >
                Upload a file
              </Typography>
              <IconButton onClick={onCloseClick}>
                <Iconify icon="mdi:close" color="text.primary" />
              </IconButton>
            </Box>
            <Stack
              direction="row"
              alignItems="center"
              gap={0.5}
              flexWrap="wrap"
            >
              <Typography typography="s2" color="text.secondary">
                Upload a file to add rows to this dataset.
              </Typography>
              <Link
                component="button"
                type="button"
                onClick={(event) => setDetailsAnchor(event.currentTarget)}
                typography="s2"
              >
                Format details
              </Link>
              <Popover
                open={Boolean(detailsAnchor)}
                anchorEl={detailsAnchor}
                onClose={() => setDetailsAnchor(null)}
                anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
              >
                <Stack spacing={0.75} sx={{ p: 1.5, width: 260 }}>
                  <Typography typography="s2" fontWeight={600}>
                    Supported formats
                  </Typography>
                  <Typography typography="s3" color="text.secondary">
                    CSV: first row is the header. JSONL: one object per line.
                    JSON: an array of objects.
                  </Typography>
                  <Link
                    href={`data:text/csv;charset=utf-8,${encodeURIComponent(SAMPLE_CSV)}`}
                    download="sample-dataset.csv"
                    typography="s3"
                  >
                    Download a sample
                  </Link>
                </Stack>
              </Popover>
            </Stack>
          </Box>
        </DialogTitle>

        <DialogContent sx={{ gap: 2, p: 0, m: "16px 0 0" }}>
          {!field?.value ? (
            <RHFUpload
              showIcon={true}
              control={control}
              name="file"
              multiple={false}
              showIllustration={false}
              accept={{
                "text/plain": [".jsonl"],
                "application/json": [".json"],
                "text/csv": [".csv"],
              }}
              onDrop={handleFileChange}
              heading="Choose a file or drag & drop it here"
              description="Supports JSONL, JSON, and CSV file format up to 25 MB"
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
              }}
              actionButton={
                <Button
                  variant="outlined"
                  size="small"
                  sx={{
                    paddingY: (theme) => theme.spacing(0.75),
                    paddingX: (theme) => theme.spacing(3),
                    borderRadius: (theme) => theme.spacing(1),
                    background: (theme) => theme.palette.divider,
                    color: "text.primary",
                    borderColor: "text.disabled",
                  }}
                >
                  Browse files
                </Button>
              }
            />
          ) : (
            <Stack spacing={1.25}>
              <UploadCard fileData={field?.value?.file} onDelete={onDelete} />
              {parsed ? (
                <>
                  <Stack direction="row" alignItems="center" gap={0.75}>
                    <Chip
                      size="small"
                      label={`${parsed.total.toLocaleString("en-US")} ${parsed.total === 1 ? "row" : "rows"}`}
                      color="primary"
                      variant="outlined"
                      data-testid="upload-row-count"
                    />
                    {parsed.columns.length > 0 && (
                      <Typography typography="s3" color="text.secondary">
                        {`Showing the first ${Math.min(8, parsed.rows.length)} · pick a role for each column`}
                      </Typography>
                    )}
                  </Stack>
                  {parsed.errors?.map((error) => (
                    <Typography
                      key={`${error.line}-${error.message}`}
                      typography="s3"
                      color="error.main"
                      role="alert"
                    >
                      {`Line ${error.line}: ${error.message}`}
                    </Typography>
                  ))}
                  {parsed.columns.length > 0 && (
                    <Box
                      sx={{
                        overflowX: "auto",
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: 1,
                      }}
                    >
                      <Box
                        component="table"
                        sx={{ borderCollapse: "collapse", width: "100%" }}
                      >
                        <thead>
                          <tr>
                            {parsed.columns.map((column) => (
                              <Box
                                component="th"
                                key={column}
                                sx={{
                                  p: 0.75,
                                  textAlign: "left",
                                  bgcolor: "background.neutral",
                                  borderBottom: "1px solid",
                                  borderColor: "divider",
                                }}
                              >
                                <Stack spacing={0.5}>
                                  <Typography
                                    typography="s3"
                                    fontWeight={600}
                                    noWrap
                                  >
                                    {column}
                                  </Typography>
                                  <TextField
                                    select
                                    size="small"
                                    value={roles[column] ?? "metadata"}
                                    onChange={(event) =>
                                      setRoles((current) => ({
                                        ...current,
                                        [column]: event.target.value,
                                      }))
                                    }
                                    sx={{
                                      minWidth: 100,
                                      "& .MuiSelect-select": {
                                        typography: "s3",
                                        py: 0.25,
                                      },
                                    }}
                                    inputProps={{
                                      "aria-label": `${column} role`,
                                    }}
                                  >
                                    {ROLE_OPTIONS.map((option) => (
                                      <MenuItem
                                        key={option.value}
                                        value={option.value}
                                        sx={{ typography: "s3" }}
                                      >
                                        {option.label}
                                      </MenuItem>
                                    ))}
                                  </TextField>
                                </Stack>
                              </Box>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {parsed.rows.slice(0, 8).map((row, rowIndex) => (
                            <tr key={rowIndex}>
                              {parsed.columns.map((column, columnIndex) => (
                                <Box
                                  component="td"
                                  key={column}
                                  sx={{
                                    p: 0.75,
                                    maxWidth: 220,
                                    whiteSpace: "nowrap",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    borderBottom: "1px solid",
                                    borderColor: "divider",
                                  }}
                                  title={row[columnIndex] ?? ""}
                                >
                                  <Typography typography="s3" noWrap>
                                    {row[columnIndex] ?? ""}
                                  </Typography>
                                </Box>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </Box>
                    </Box>
                  )}
                  {parsed.problem && (
                    <Typography
                      typography="s2"
                      color="error.main"
                      role="alert"
                      data-testid="upload-problem"
                    >
                      {parsed.problem}
                    </Typography>
                  )}
                  {Object.entries(ROLE_HINTS)
                    .filter(([role]) => Object.values(roles).includes(role))
                    .map(([role, hint]) => (
                      <Typography
                        key={role}
                        typography="s3"
                        color="text.secondary"
                      >
                        {hint}
                      </Typography>
                    ))}
                  <ImportFileExtras
                    text={source?.text ?? ""}
                    fileName={source?.name}
                    parsed={parsed}
                    flatten={flatten}
                    parseJson={parseJson}
                    onFlatten={setFlatten}
                    onParseJson={setParseJson}
                  />
                  {summary && (
                    <Stack
                      gap={0.5}
                      sx={{
                        p: 1,
                        borderRadius: 1,
                        bgcolor: "background.neutral",
                      }}
                      data-testid="upload-im1-summary"
                    >
                      <Typography typography="s2" fontWeight={600}>
                        {importSummaryText(summary, idColumn)}
                      </Typography>
                      {importNotes(summary).map((line) => (
                        <Typography
                          key={line}
                          typography="s3"
                          color={
                            /schema/.test(line)
                              ? "error.main"
                              : "text.secondary"
                          }
                        >
                          {line}
                          {/schema/.test(line) && invalidHref ? (
                            <>
                              {" · "}
                              <Link
                                href={invalidHref}
                                download="rows-that-dont-match.jsonl"
                                typography="s3"
                              >
                                Download them
                              </Link>
                            </>
                          ) : null}
                        </Typography>
                      ))}
                      {transformNotes(preview.data?.items).map((x) => (
                        <Typography
                          key={x.message}
                          typography="s3"
                          color="text.secondary"
                          data-testid="upload-transform-note"
                        >
                          {`${x.message} · ${x.rows} ${x.rows === 1 ? "row" : "rows"}`}
                        </Typography>
                      ))}
                      {summary.invalid > 0 &&
                        topIssues(preview.data?.items).map((x) => (
                          <Typography
                            key={x.message}
                            typography="s3"
                            color="text.secondary"
                            sx={{ fontFamily: "monospace" }}
                          >
                            {`${x.message} (${x.rows} ${x.rows === 1 ? "row" : "rows"})`}
                          </Typography>
                        ))}
                      {summary.archived_matches > 0 && (
                        <Stack
                          direction="row"
                          alignItems="center"
                          gap={1}
                          flexWrap="wrap"
                          data-testid="upload-archived-matches"
                        >
                          <Typography typography="s3">
                            {archivedMatchText(summary)}
                          </Typography>
                          <RadioGroup
                            row
                            value={archivedMatches}
                            onChange={(e) => setArchivedMatches(e.target.value)}
                          >
                            <FormControlLabel
                              value="keep"
                              control={<Radio size="small" />}
                              label={
                                <Typography typography="s3">
                                  {summary.archived_matches === 1
                                    ? "Keep it archived"
                                    : "Keep them archived"}
                                </Typography>
                              }
                            />
                            <FormControlLabel
                              value="restore"
                              control={<Radio size="small" />}
                              label={
                                <Typography typography="s3">
                                  {summary.archived_matches === 1
                                    ? "Restore it"
                                    : "Restore them"}
                                </Typography>
                              }
                            />
                          </RadioGroup>
                        </Stack>
                      )}
                      {summary.not_in_file > 0 && (
                        <Stack
                          direction="row"
                          alignItems="center"
                          gap={1}
                          flexWrap="wrap"
                          data-testid="upload-not-in-file"
                        >
                          <Typography typography="s3">
                            {`${summary.not_in_file.toLocaleString("en-US")} ${summary.not_in_file === 1 ? "row" : "rows"} of the dataset ${summary.not_in_file === 1 ? "is" : "are"} not in the file:`}
                          </Typography>
                          <RadioGroup
                            row
                            value={notInFile}
                            onChange={(e) => setNotInFile(e.target.value)}
                          >
                            <FormControlLabel
                              value="keep"
                              control={<Radio size="small" />}
                              label={
                                <Typography typography="s3">
                                  Keep them
                                </Typography>
                              }
                            />
                            <FormControlLabel
                              value="archive"
                              control={<Radio size="small" />}
                              label={
                                <Typography typography="s3">
                                  Archive them
                                </Typography>
                              }
                            />
                            <FormControlLabel
                              value="delete"
                              control={<Radio size="small" />}
                              label={
                                <Typography typography="s3">
                                  Delete them
                                </Typography>
                              }
                            />
                          </RadioGroup>
                        </Stack>
                      )}
                    </Stack>
                  )}
                </>
              ) : null}
            </Stack>
          )}
        </DialogContent>
        <DialogActions sx={{ padding: 0, margin: "32px 0 0" }}>
          <Button size="small" onClick={onCloseClick} variant="outlined">
            Cancel
          </Button>
          {field?.value && parsed && (
            <CustomTooltip
              show={Boolean(blockReason)}
              size="small"
              placement="top"
              title={blockReason ?? ""}
            >
              <Box component="span" data-testid="upload-add-wrap">
                <LoadingButton
                  onClick={handleSubmit(onSubmit)}
                  variant="contained"
                  autoFocus
                  size="small"
                  color="primary"
                  loading={isPending}
                  disabled={Boolean(blockReason)}
                >
                  {addButtonLabel(
                    summary,
                    parsed.total,
                    notInFile,
                    archivedMatches,
                  )}
                </LoadingButton>
              </Box>
            </CustomTooltip>
          )}
        </DialogActions>
      </Box>
    </Dialog>
  );
};

function UploadFileModal(props) {
  // Both services and a versioned writer are needed for pending operations.
  // The classic branch does not mount the staged component's query hooks.
  const staged =
    !gridWritesClassic() &&
    isSurfaceShown("dataset.imports.sources") &&
    isSurfaceShown("dataset.versions");
  return staged ? (
    <StagedUploadFileModal {...props} />
  ) : (
    <ClassicRowUploadModal {...props} />
  );
}

StagedUploadFileModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  closeDrawer: PropTypes.func,
};

export default UploadFileModal;
