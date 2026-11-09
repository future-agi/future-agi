import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useWatch } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { IMPORTS_PATH } from "src/sections/common/AddData/mappingModel";
import { v1LineText } from "src/sections/develop/AddDatasetDrawer/v1Line";
import { DatasetInfo, FaceBadge } from "./huggingFaceStyle";
import Iconify from "src/components/iconify";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import LinkifiedTypography from "src/components/LinkifiedTypography/LinkifiedTypography";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  hfDirectAddHint,
  hfPreviewAvailable,
  hfRequestReady,
  hfRevisionChoices,
  hfSubmitLabel,
} from "./hfLaunchModel";

/**
 * W3-7: what the import will do, from the server (IM1 `huggingface`
 * preview, which writes nothing): the v1 a create makes, or the pending
 * rows (and the columns created now) an add makes.
 */
function useHfPreview({
  control,
  repo,
  revision,
  targetDatasetId,
  enabled,
  splitRows,
}) {
  const [name, config, split, numRows] = useWatch({
    control,
    name: [
      "name",
      "huggingface_dataset_config",
      "huggingface_dataset_split",
      "num_rows",
    ],
  });
  const rows = Number(numRows);
  // More rows than the split has: say so here (no request).
  const tooMany =
    splitRows != null && Number.isInteger(rows) && rows > splitRows
      ? `The ${split} split has ${splitRows.toLocaleString("en-US")} rows.`
      : null;
  const body = {
    op: "preview",
    sample: 1,
    source: {
      kind: "huggingface",
      repo,
      revision,
      config,
      split,
      num_rows: Number.isInteger(rows) && rows > 0 ? rows : undefined,
    },
    target: targetDatasetId
      ? { dataset_id: targetDatasetId }
      : { new: { name: String(name ?? "").trim() } },
  };
  const q = useQuery({
    queryKey: ["hf-import-preview", body],
    queryFn: () => axios.post(IMPORTS_PATH, body).then((r) => r?.data?.result),
    enabled: Boolean(
      enabled &&
        hfPreviewAvailable() &&
        !tooMany &&
        repo &&
        config &&
        split &&
        (targetDatasetId || String(name ?? "").trim()),
    ),
    staleTime: 30000,
    retry: false,
    // The form shows the problem; no global toast.
    meta: { errorHandled: true },
  });
  if (tooMany) return { text: null, problem: tooMany };
  const problem = q.error
    ? getRequestErrorMessage(q.error, "This import can't run.")
    : null;
  if (!q.data) return { text: null, problem };
  if (!targetDatasetId) return { text: v1LineText(q.data.v1), problem };
  const add = q.data.summary?.add ?? 0;
  const newCols = q.data.new_columns ?? [];
  return {
    text: [
      `Adds ${add.toLocaleString("en-US")} ${add === 1 ? "row" : "rows"} as pending changes; Save version keeps them`,
      newCols.length
        ? `${newCols.length === 1 ? "1 new column is" : `${newCols.length} new columns are`} created now: ${newCols.join(", ")}`
        : null,
    ]
      .filter(Boolean)
      .join(" · "),
    problem,
  };
}

const HuggingDetailForm = ({
  subsetOptions,
  splitOptions,
  huggingFaceDetail,
  onSubmit,
  onClose,
  isLoadingCreateDataset,
  showNameField,
  control,
  huggingFaceDatasetConfigError,
  config,
  revision,
  onRevision,
  targetDatasetId,
}) => {
  const theme = useTheme();
  const [subset, split] = useWatch({
    control,
    name: ["huggingface_dataset_config", "huggingface_dataset_split"],
  });
  const splitRows = config?.dataset_info?.num_rows?.[subset]?.[split];
  const preview = useHfPreview({
    control,
    repo: huggingFaceDetail?.id,
    revision: config?.revision,
    targetDatasetId,
    enabled: Boolean(config),
    splitRows,
  });
  // L5 (hf:drawer): revisions only where a route takes one (prototype).
  const revisions = hfRevisionChoices(config);
  const directAddHint =
    targetDatasetId && !hfPreviewAvailable() ? hfDirectAddHint : null;

  return (
    <Stack flexDirection="column" p="16px" height="100%">
      <Typography variant="m3" fontWeight={500} color="text.primary" mb="29px">
        Hugging Face Dataset
      </Typography>
      <DatasetInfo>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent={"space-between"}
          paddingTop={"3px"}
          paddingBottom={"11px"}
          paddingLeft={"14px"}
          paddingRight={"11px"}
          sx={{ borderBottom: `1px solid ${theme.palette.divider}` }}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Dataset Information
          </Typography>
          <Box sx={{ display: "flex", alignItems: "center", gap: "5px" }}>
            <Iconify
              icon="material-symbols:download"
              sx={{ color: "text.disabled" }}
            />
            <Typography
              component="span"
              sx={{
                color: "text.disabled",
                textAlign: "center",
                fontSize: "10px",
              }}
            >
              {huggingFaceDetail?.downloads}
            </Typography>
            <Iconify
              icon="weui:like-outlined"
              sx={{ color: "text.disabled" }}
            />
            <Typography
              variant="span"
              component="span"
              sx={{
                color: "text.disabled",
                textAlign: "center",
                fontSize: "10px",
              }}
            >
              {huggingFaceDetail?.likes}
            </Typography>
          </Box>
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent={"space-between"}
          paddingTop={"8px"}
          paddingBottom={"14px"}
          paddingLeft={"14px"}
          paddingRight={"11px"}
          sx={{ borderBottom: `1px solid ${theme.palette.divider}` }}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Dataset
          </Typography>
          <Typography
            variant="p"
            component="span"
            sx={{
              color: "text.disabled",
              textAlign: "center",
              fontWeight: "500",
              fontSize: "12px",
              textDecoration: "underline",
            }}
          >
            {huggingFaceDetail?.name}
          </Typography>
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent={"space-between"}
          paddingTop={"10px"}
          paddingLeft={"14px"}
          paddingRight={"11px"}
          paddingBottom={"14px"}
          sx={{ borderBottom: `1px solid ${theme.palette.divider}` }}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Author
          </Typography>
          <FaceBadge color={"#2F7CF7"} bgcolor={"#2F7CF729"}>
            {huggingFaceDetail?.author}
          </FaceBadge>
        </Stack>
        {huggingFaceDetail?.license ? (
          <Stack
            direction="row"
            alignItems="center"
            justifyContent={"space-between"}
            paddingTop={"10px"}
            paddingLeft={"14px"}
            paddingRight={"11px"}
            paddingBottom={"14px"}
            sx={{ borderBottom: `1px solid ${theme.palette.divider}` }}
          >
            <Typography
              variant="s1"
              color="text.primary"
              fontWeight="fontWeightMedium"
            >
              Licence
            </Typography>
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="hf-license"
            >
              {huggingFaceDetail.license}
            </Typography>
          </Stack>
        ) : null}
        <Stack
          paddingTop={"10px"}
          paddingBottom={"14px"}
          paddingLeft={"14px"}
          paddingRight={"11px"}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Description{" "}
          </Typography>
          <Typography
            sx={{
              color: "text.disabled",
              fontWeight: "400",
              fontSize: "14px",
              pt: "6px",
            }}
          >
            {huggingFaceDetail?.description}
          </Typography>
        </Stack>
      </DatasetInfo>
      <Typography
        variant="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
        mb="15px"
      >
        Ingest Dataset
      </Typography>
      <Box
        sx={{
          flex: 1,
          display: "flex",
          gap: 2,
          flexDirection: "column",
          paddingY: 1,
        }}
      >
        {showNameField && (
          <Box display="flex" flexDirection="column">
            <FormTextFieldV2
              disabled={huggingFaceDatasetConfigError?.length}
              control={control}
              size="small"
              fieldName="name"
              label="Name"
              placeholder="Enter name"
              variant="outlined"
              sx={{ mb: 0.5 }}
              margin="none"
              required
            />
            <Typography
              color="text.secondary"
              variant="s3"
              fontWeight="fontWeightMedium"
              sx={{ mt: 0, mb: 2 }}
            >
              Name your dataset
            </Typography>
          </Box>
        )}

        {onRevision && !hfPreviewAvailable() ? (
          <TextField
            size="small"
            label="Revision"
            value={revision ?? ""}
            placeholder="main"
            onChange={(event) => onRevision(event.target.value)}
            helperText="Branch, tag, or commit. Imports use the resolved commit."
            sx={{ mb: 2 }}
          />
        ) : null}
        {revisions.length ? (
          <Box display="flex" flexDirection="column">
            <TextField
              select
              size="small"
              label="Revision"
              value={revision ?? config.revision}
              onChange={(e) => onRevision?.(e.target.value)}
              SelectProps={{
                SelectDisplayProps: { "data-testid": "hf-revision" },
              }}
              sx={{ mb: 0.5 }}
            >
              {revisions.map((r) => (
                <MenuItem key={r.sha} value={r.sha}>
                  {`${r.label} · ${new Date(r.date).toLocaleDateString(
                    "en-US",
                    { month: "short", day: "numeric", year: "numeric" },
                  )} · ${r.num_rows.toLocaleString("en-US")} rows`}
                </MenuItem>
              ))}
            </TextField>
            <Typography
              color="text.secondary"
              variant="s3"
              fontWeight="fontWeightMedium"
              sx={{ mt: 0, mb: 2 }}
            >
              The commit to import from; the dataset notes it
            </Typography>
          </Box>
        ) : null}

        <Box display="flex" flexDirection="column">
          <FormSearchSelectFieldControl
            fullWidth
            control={control}
            fieldName="huggingface_dataset_config"
            size="small"
            label="Subset"
            options={subsetOptions}
            disabled={huggingFaceDatasetConfigError?.length}
            sx={{ mb: 0.5 }}
            required
          />
          <Typography
            color="text.secondary"
            variant="s3"
            fontWeight="fontWeightMedium"
            sx={{ mt: 0, mb: 2 }}
          >
            Select which subset of the dataset you want to import
          </Typography>
        </Box>

        <Box display="flex" flexDirection="column">
          <FormSearchSelectFieldControl
            fullWidth
            control={control}
            fieldName="huggingface_dataset_split"
            size="small"
            label="Split"
            options={splitOptions}
            disabled={huggingFaceDatasetConfigError?.length}
            sx={{ mb: 0.5 }}
            required
          />
          <Typography
            color="text.secondary"
            variant="s3"
            fontWeight="fontWeightMedium"
            sx={{ mt: 0, mb: 2 }}
          >
            Select the data split you want to import
          </Typography>
        </Box>

        {huggingFaceDatasetConfigError && (
          <LinkifiedTypography
            text={huggingFaceDatasetConfigError}
            color={"red.500"}
            variant="s2"
            fontWeight={"fontWeightRegular"}
          />
        )}

        <Box display="flex" flexDirection="column">
          <FormTextFieldV2
            disabled={huggingFaceDatasetConfigError?.length}
            control={control}
            fieldName="num_rows"
            size="small"
            label="No. of rows"
            variant="outlined"
            fullWidth
            isSpinnerField
            placeholder="Enter number of rows"
            fieldType="number"
            required
          />
          <Typography
            color="text.secondary"
            variant="s3"
            fontWeight="fontWeightMedium"
            sx={{ mt: 0, mb: 2 }}
          >
            {splitRows != null
              ? `The ${split} split has ${splitRows.toLocaleString("en-US")} rows; the first ones are imported`
              : "Choose the number of rows to import"}
          </Typography>
        </Box>
        {preview.text ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="hf-import-preview"
          >
            {preview.text}
          </Typography>
        ) : null}
        {directAddHint ? (
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="hf-direct-add-hint"
          >
            {directAddHint}
          </Typography>
        ) : null}
        {preview.problem ? (
          <Typography
            typography="s2"
            color="error.main"
            data-testid="hf-import-problem"
          >
            {preview.problem}
          </Typography>
        ) : null}
      </Box>
      <Box
        sx={{
          display: "flex",
          gap: 2,
          width: "100%",
          paddingBottom: "16px",
          marginTop: "auto",
        }}
      >
        <Button
          onClick={() => {
            onClose();
          }}
          fullWidth
          variant="outlined"
        >
          Cancel
        </Button>
        <LoadingButton
          disabled={
            Boolean(huggingFaceDatasetConfigError?.length) ||
            !hfRequestReady(config, revision) ||
            Boolean(preview.problem)
          }
          fullWidth
          variant="contained"
          color="primary"
          loading={isLoadingCreateDataset}
          onClick={() => {
            trackEvent(Events.dataAddSuccessfull, {
              [PropertyName.method]: " add using import from hugging face",
            });
            onSubmit();
          }}
        >
          {hfSubmitLabel(targetDatasetId)}
        </LoadingButton>
      </Box>
    </Stack>
  );
};

HuggingDetailForm.propTypes = {
  subsetOptions: PropTypes.array,
  splitOptions: PropTypes.array,
  huggingFaceDetail: PropTypes.object,
  onSubmit: PropTypes.func,
  onClose: PropTypes.func,
  isLoadingCreateDataset: PropTypes.bool,
  showNameField: PropTypes.bool,
  control: PropTypes.object.isRequired,
  huggingFaceDatasetConfigError: PropTypes.string,
  config: PropTypes.object,
  revision: PropTypes.string,
  onRevision: PropTypes.func,
  targetDatasetId: PropTypes.string,
};

export default HuggingDetailForm;
