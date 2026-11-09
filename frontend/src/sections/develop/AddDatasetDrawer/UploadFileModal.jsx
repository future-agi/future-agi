import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { v1LineText } from "./v1Line";
import { useEffect, useMemo, useState } from "react";
import { useController, useForm, useWatch } from "react-hook-form";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import UploadedFileIllustration from "./UploadedFileIllustration";
import { zodResolver } from "@hookform/resolvers/zod";
import { UploadFileValidationSchema } from "./validation";
import { RHFUpload } from "src/components/hook-form";
import axios from "src/utils/axios";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useFileUploadLifecycle } from "./useFileUploadLifecycle";
import { createUploadUndo } from "./uploadUndo";
import { IMPORTS_PATH } from "src/sections/common/AddData/mappingModel";
import { fileImportBody } from "../AddRowDrawer/fileImport";
import ImportFileExtras from "./ImportFileExtras";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import { useNavigate } from "react-router";
import { ConfirmDialog } from "src/components/custom-dialog";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { notify } from "src/sections/common/ux/toast";
import { fmtCount } from "src/sections/common/format/fmt";
import ImportPreviewPane from "./ImportPreviewPane";
import { isPrototypeMode } from "src/utils/prototype";
import {
  effectiveRoles,
  rolesOffered,
  uploadAccepts,
  uploadFormData,
  uploadServerBlock,
} from "./uploadBody";
import {
  extOf,
  fileProblem,
  fmtBytes,
  guessRole,
  importBlockReason,
  parseImport,
  SAMPLE_CSV,
  SAMPLE_CSV_NAME,
} from "./importFile";

/** Saves the sample CSV (no server round trip). */
const downloadSampleCsv = () => {
  const url = URL.createObjectURL(
    new Blob([SAMPLE_CSV], { type: "text/csv;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = SAMPLE_CSV_NAME;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
};

const defaultValues = {
  name: "",
  file: null,
  modelType: "GenerativeLLM",
};

// Roles that become a column of the new dataset (split and tags do not).
const COLUMN_ROLES = new Set(["input", "expected", "metadata", "id"]);

const UploadFileModal = ({ open, onClose, refreshGrid, onDone }) => {
  // DL#1, DL#2: checked + parsed in the browser before upload.
  const [problem, setProblem] = useState(null);
  const [parsed, setParsed] = useState(null);
  const [roles, setRoles] = useState({});
  // W3-1: the file text, so the toggles re-read it with the ONE parser.
  const [source, setSource] = useState(null);
  const [flatten, setFlatten] = useState(false);
  const [parseJson, setParseJson] = useState(false);
  useEffect(() => {
    if (!source) return;
    const p = parseImport(source.text, extOf(source.name), {
      flatten,
      parseJson,
    });
    setParsed(p);
    setRoles((cur) =>
      Object.fromEntries(p.columns.map((c) => [c, cur[c] ?? guessRole(c)])),
    );
  }, [source, flatten, parseJson]);
  const {
    control,
    handleSubmit,
    reset,
    setValue,
    getValues,
    formState: { isDirty },
  } = useForm({
    defaultValues,
    resolver: zodResolver(UploadFileValidationSchema),
  });

  const queryClient = useQueryClient();
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const navigate = useNavigate();

  const {
    uploadDataset,
    isPending,
    pendingDatasetId,
    clearUpload,
    progressText,
    importError,
    retryProgress,
  } = useFileUploadLifecycle({
    onSuccess: (data, form) => {
      const id = data?.data?.result?.dataset_id;
      const name = form?.get?.("new_dataset_name") ?? "dataset";
      const n = data?.data?.result?.rows ?? parsed?.total ?? 0;
      const toast = notify(`Created "${name}" · ${fmtCount(n, "row")}`, {
        variant: "success",
        key: "dataset-created",
        undo: { onUndo: () => {}, ms: 10000 },
      });
      const removeDataset = createUploadUndo({
        id,
        name,
        queryClient,
        navigate,
      });
      toast.undone.then((undone) => {
        if (undone) return removeDataset();
      });
      queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
      queryClient.invalidateQueries({ queryKey: ["datasets", "query"] });
      queryClient.invalidateQueries({ queryKey: ["get-dataset-detail", id] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-list"],
      });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      queryClient.invalidateQueries({ queryKey: ["dataset-detail"] });
      trackEvent(Events.datasetFromJSONCSVSuccessful, {
        [PropertyName.datasetId]: data?.data?.result?.dataset_id,
      });
      onCloseClick(null, true);
      reset();
      setParsed(null);
      refreshGrid?.();
      onDone?.();
      // Navigate after the dialog's close commits: the lazy dataset page
      // suspends, and a suspended update would keep the dialog painted.
      setTimeout(() => navigate(`/dashboard/develop/${id}?tab=data`), 0);
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to upload dataset", {
          retryAction: "uploading this dataset file",
        }),
        { variant: "error" },
      );
    },
  });

  const { field } = useController({
    name: "file",
    control,
  });

  const handleFileChange = (acceptedFiles) => {
    const file = acceptedFiles[0];
    if (!file) return;
    const bad = fileProblem(file);
    setProblem(bad);
    setParsed(null);
    if (bad) return;
    file
      .text?.()
      .then((text) => {
        const p = parseImport(text, extOf(file.name));
        setRoles({});
        setSource({ name: file.name, text });
        if (!p.total && !p.errors.length)
          setProblem(
            "The file has no rows. Add a header line and at least one row.",
          );
      })
      .catch(() =>
        setProblem("Couldn't read the file. Try saving it again as UTF-8."),
      );
    field.onChange({
      file,
      preview: <UploadedFileIllustration width={100} height={100} />,
    });

    // Auto-populate dataset name from filename if name is empty
    const currentName = getValues("name");
    if (!currentName && file?.name) {
      const nameWithoutExtension = file.name.replace(/\.[^/.]+$/, "");
      const cleanedName = nameWithoutExtension
        .replace(/[^a-zA-Z0-9\s-_]/g, "")
        .replace(/\s+/g, " ")
        .trim();
      if (cleanedName) {
        // Check for duplicate names in existing datasets
        const cachedData = queryClient.getQueryData([
          "develop",
          "dataset-name-list",
        ]);
        const existingNames = (cachedData?.data?.result?.datasets ?? []).map(
          (d) => d.name?.toLowerCase(),
        );

        let finalName = cleanedName;
        if (existingNames.includes(finalName.toLowerCase())) {
          let version = 2;
          while (
            existingNames.includes(`${cleanedName} v${version}`.toLowerCase())
          ) {
            version++;
          }
          finalName = `${cleanedName} v${version}`;
        }

        setValue("name", finalName, {
          shouldDirty: true,
          shouldValidate: true,
        });
      }
    }
  };

  // L2: where dev keeps no column roles, no role picker is shown and every
  // column of the file is imported (uploadBody.js effectiveRoles).
  const rolesShown = rolesOffered();
  const actRoles = effectiveRoles(roles);
  const isUploadBlocked = isPending || Boolean(pendingDatasetId);
  const onSubmit = (data) => {
    if (isUploadBlocked) return;
    // L2: only the fields dev's strict upload route declares (uploadBody.js).
    if (
      uploadServerBlock({
        roles: actRoles,
        columns: parsed?.columns ?? [],
        flatten,
        parseJson,
      })
    )
      return;
    const formData = uploadFormData({
      name: data.name,
      modelType: data.modelType,
      file: data.file.file,
      roles: actRoles,
      flatten,
      parseJson,
    });
    //@ts-ignore
    uploadDataset(formData);
  };

  const onCloseClick = (_, skipConfirmation = false) => {
    if (isPending && !skipConfirmation) return;
    if (!skipConfirmation && isDirty && !pendingDatasetId) {
      setIsDialogOpen(true);
    } else {
      if (pendingDatasetId && !skipConfirmation) {
        // Media failed, but the table is saved and must be discoverable.
        queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
        queryClient.invalidateQueries({ queryKey: ["datasets", "query"] });
        queryClient.invalidateQueries({
          queryKey: ["develop", "dataset-list"],
        });
        queryClient.invalidateQueries({
          queryKey: ["develop", "dataset-name-list"],
        });
        refreshGrid?.();
      }
      clearUpload();
      reset();
      setParsed(null);
      setProblem(null);
      onClose();
    }
  };

  const handleDialogClose = (confirm) => {
    if (confirm) {
      reset();
      onClose();
    }
    setIsDialogOpen(false);
  };

  const onDelete = () => {
    setValue("file", null);
    setParsed(null);
    setProblem(null);
    setSource(null);
  };
  const fileObj = field?.value?.file;
  const importCount = parsed?.total ?? 0;
  const keptColumns = (parsed?.columns ?? []).filter((c) =>
    COLUMN_ROLES.has(actRoles[c] ?? "metadata"),
  );
  const nameValue = useWatch({ control, name: "name" });
  // W3-7: the door says what it will create before it creates it (IM1
  // preview with `target.new`: rows, columns and the v1 note).
  const v1Body = useMemo(
    () =>
      source && parsed && !parsed.problem && parsed.total
        ? fileImportBody({
            newDataset: { name: String(nameValue ?? "").trim() },
            fileName: source.name,
            text: source.text,
            roles,
            flatten,
            parseJson,
          })
        : null,
    [source, parsed, nameValue, roles, flatten, parseJson],
  );
  const v1 = useQuery({
    queryKey: [
      "im1-v1-preview",
      source?.name,
      source?.text?.length,
      String(nameValue ?? "").trim(),
      roles,
      flatten,
      parseJson,
    ],
    queryFn: () =>
      axios
        .post(IMPORTS_PATH, { op: "preview", ...v1Body })
        .then((r) => r?.data?.result?.v1 ?? null),
    // L2: the IM1 preview route exists only in the prototype's mocks.
    enabled: Boolean(
      isPrototypeMode() && open && v1Body && String(nameValue ?? "").trim(),
    ),
    staleTime: 30000,
    retry: false,
  });
  // L2: a choice dev cannot honour blocks Import with its reason.
  const serverBlock = parsed
    ? uploadServerBlock({
        roles: actRoles,
        columns: parsed?.columns ?? [],
        flatten,
        parseJson,
      })
    : null;
  const blockReason =
    importBlockReason({
      name: nameValue,
      hasFile: Boolean(fileObj),
      problem,
      parsed,
      keptColumns: keptColumns.length,
    }) ?? serverBlock;

  return (
    <Dialog
      open={open}
      onClose={() => onCloseClick()}
      maxWidth={parsed ? "lg" : "sm"}
      fullWidth
      aria-labelledby="upload-file-dialog-title"
      aria-describedby="upload-file-dialog-description"
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle
          sx={{
            gap: "2px",
            display: "flex",
            flexDirection: "column",
            padding: 0,
            margin: 0,
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              id="upload-file-dialog-title"
              fontWeight={"fontWeightBold"}
              color="text.primary"
              variant="h5"
              sx={{ fontSize: "20px" }}
            >
              Upload a file
            </Typography>
            <IconButton
              aria-label="Close upload"
              onClick={() => onCloseClick()}
              disabled={isPending}
            >
              <Iconify icon="mdi:close" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText
            id="upload-file-dialog-description"
            text={
              parsed
                ? rolesShown
                  ? "Check the first rows and each column's role. Nothing is created until you press Import."
                  : "Check the first rows. Every column is imported. Nothing is created until you press Import."
                : rolesShown
                  ? "CSV, JSON or JSONL, up to 25 MB. After you choose a file you'll see its first rows and pick each column's role, before anything is created."
                  : "CSV, JSON or JSONL, up to 25 MB. After you choose a file you'll see its first rows before anything is created."
            }
          />
        </DialogTitle>

        <DialogContent sx={{ padding: 0 }}>
          {progressText && (
            <Typography
              typography="s2"
              color="text.secondary"
              role="status"
              sx={{ mt: 2 }}
            >
              {progressText}
            </Typography>
          )}
          {importError && (
            <Box sx={{ mt: 2 }}>
              <Typography typography="s2" color="error.main" role="alert">
                {importError}
              </Typography>
              {pendingDatasetId && (
                <Button onClick={() => retryProgress()}>
                  Check progress again
                </Button>
              )}
            </Box>
          )}
          <Box
            component="fieldset"
            disabled={isUploadBlocked}
            sx={{
              border: 0,
              padding: 0,
              margin: 0,
              minWidth: 0,
              display: "flex",
              flexDirection: "column",
              gap: 2,
              marginTop: 2,
            }}
          >
            <FormTextFieldV2
              fieldName="name"
              control={control}
              label="Name"
              size="small"
              required
              placeholder="Dataset name"
              fullWidth
              rules={{ required: "Name is required" }}
            />

            {!field?.value ? (
              <RHFUpload
                showIcon={true}
                control={control}
                name="file"
                multiple={false}
                showIllustration={false}
                // Every type reaches handleFileChange, which explains a wrong
                // one inline (DL#2) instead of dropping it silently.
                accept={undefined}
                onDrop={handleFileChange}
                heading="Choose a file or drag & drop it here"
                description="Checked in your browser first. Nothing uploads until you press Import."
                actionButton={
                  <Button
                    variant="outlined"
                    color="primary"
                    size="small"
                    startIcon={
                      <Iconify icon="mdi:folder-open-outline" width={16} />
                    }
                    sx={{
                      paddingY: (theme) => theme.spacing(0.75),
                      paddingX: (theme) => theme.spacing(2.5),
                      borderRadius: (theme) => theme.spacing(1),
                    }}
                  >
                    Browse files
                  </Button>
                }
              />
            ) : (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  flexWrap: "wrap",
                }}
              >
                <Box
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.75,
                    px: 1,
                    py: 0.5,
                    borderRadius: 1,
                    border: 1,
                    borderColor: problem ? "error.main" : "divider",
                    typography: "s2",
                  }}
                >
                  <Iconify icon="mdi:file-document-outline" width={16} />
                  {fileObj?.name} · {fmtBytes(fileObj?.size)}
                  <IconButton
                    size="small"
                    aria-label="Remove file"
                    onClick={onDelete}
                    sx={{ p: 0.25 }}
                  >
                    <Iconify icon="mdi:close" width={14} />
                  </IconButton>
                </Box>
                {parsed && (
                  <Typography typography="s2" color="text.secondary">
                    {fmtCount(parsed.total, "row")},{" "}
                    {fmtCount(parsed.columns.length, "column")},{" "}
                    {fmtBytes(fileObj?.size)}
                  </Typography>
                )}
              </Box>
            )}
            {!parsed && (
              <Typography typography="s2" color="text.secondary">
                Not sure about the columns?{" "}
                <CustomTooltip
                  show
                  size="small"
                  placement="bottom"
                  title="3 rows: customer_message (Input), expected_reply (Expected), intent and language (Metadata)"
                >
                  <Link
                    component="button"
                    type="button"
                    typography="s2"
                    underline="hover"
                    onClick={downloadSampleCsv}
                    sx={{ verticalAlign: "baseline" }}
                  >
                    Download a sample CSV
                  </Link>
                </CustomTooltip>
              </Typography>
            )}
            {problem && (
              <Typography typography="s2" color="error.main" role="alert">
                {problem}
              </Typography>
            )}
            {parsed && !problem && parsed.problem && (
              <Typography
                typography="s2"
                color="error.main"
                role="alert"
                data-testid="upload-problem"
              >
                {parsed.problem}
              </Typography>
            )}
            {parsed && !problem && (
              <ImportPreviewPane
                parsed={parsed}
                roles={actRoles}
                onRole={
                  rolesShown
                    ? (c, r) => setRoles((cur) => ({ ...cur, [c]: r }))
                    : undefined
                }
              />
            )}
            {parsed && !problem && (
              <ImportFileExtras
                text={source?.text ?? ""}
                fileName={source?.name}
                parsed={parsed}
                flatten={flatten}
                parseJson={parseJson}
                onFlatten={setFlatten}
                onParseJson={setParseJson}
                optionsShown={uploadAccepts("import_options")}
              />
            )}
            {parsed && !problem && serverBlock ? (
              <Typography
                typography="s2"
                color="error.main"
                role="alert"
                data-testid="upload-server-block"
              >
                {serverBlock}
              </Typography>
            ) : null}
            {v1.data && !blockReason && (
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="upload-v1-note"
              >
                {v1LineText(v1.data)}
              </Typography>
            )}
            {isPending && (
              <Typography typography="s2" color="text.secondary">
                Creating {fmtCount(importCount, "row")}…
              </Typography>
            )}
          </Box>
        </DialogContent>

        <DialogActions
          disableSpacing
          sx={{
            padding: 0,
            marginTop: 4,
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 1,
          }}
        >
          <Button
            fullWidth
            onClick={() => onCloseClick()}
            variant="outlined"
            disabled={isPending}
          >
            {/* <Typography
              variant="s2"
              width={"80px"}
              fontWeight={"fontWeightSemiBold"}
              color="text.secondary"
            > */}
            Cancel
            {/* </Typography> */}
          </Button>
          <CustomTooltip
            show={Boolean(blockReason) && !isPending}
            size="small"
            placement="top"
            title={blockReason ?? ""}
          >
            {/* A disabled button gets no hover: the span carries the tooltip. */}
            <Box
              component="span"
              sx={{ display: "flex", minWidth: 0 }}
              data-testid="upload-import-wrap"
            >
              <LoadingButton
                onClick={handleSubmit(onSubmit)}
                variant="contained"
                fullWidth
                autoFocus
                color="primary"
                loading={isPending}
                disabled={isUploadBlocked || Boolean(blockReason)}
              >
                {parsed ? `Import ${fmtCount(importCount, "row")}` : "Import"}
              </LoadingButton>
            </Box>
          </CustomTooltip>
        </DialogActions>
      </Box>

      <ConfirmDialog
        open={isDialogOpen}
        title="Discard this upload?"
        message="Close without importing?"
        content={
          rolesShown
            ? "The file and the roles you picked will be lost."
            : "The file you picked will be lost."
        }
        onClose={() => handleDialogClose(false)}
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => handleDialogClose(true)}
          >
            Confirm
          </Button>
        }
      />
    </Dialog>
  );
};

UploadFileModal.propTypes = {
  onDone: PropTypes.func,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
};

export default UploadFileModal;
