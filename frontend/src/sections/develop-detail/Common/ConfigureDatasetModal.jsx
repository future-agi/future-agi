import { zodResolver } from "@hookform/resolvers/zod";
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import SvgColor from "src/components/svg-color";
import { useParams } from "src/routes/hooks";
import DeleteDataset from "src/sections/develop/DeleteDataset/DeleteDataset";
import AccessSection from "src/sections/common/Access/AccessSection";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import { deleteReason as deleteReasonFor } from "src/sections/common/Access/accessModel";
import axios, { endpoints } from "src/utils/axios";
import { z } from "zod";

const DESCRIPTION_MAX = 500;
const TAG_RE = /^[a-z0-9][a-z0-9._-]{0,29}$/;
const TAG_HINT =
  "Lowercase letters, digits, dots, dashes or underscores; up to 30 characters.";

const schema = z.object({
  datasetName: z.string().trim().min(1, "Dataset name is required"),
  description: z
    .string()
    .max(DESCRIPTION_MAX, `Up to ${DESCRIPTION_MAX} characters`),
  tags: z.array(z.string().regex(TAG_RE, TAG_HINT)).max(20),
});

/**
 * Configure / Edit details (PRODUCT-SPEC §3.1). Name, Description (500
 * characters) and Tags (free-entry chips, `^[a-z0-9][a-z0-9._-]{0,29}$`);
 * saves through `edit_dataset_behavior`. Used by the dataset header
 * (Configure) and the dataset list's row menu (Edit details…, `datasetId`).
 */
const ConfigureDatasetModal = ({
  open,
  onClose,
  datasetName = "",
  description,
  tags,
  title = "Configure Dataset",
  datasetId,
  onSaved,
}) => {
  const theme = useTheme();
  const typographyTheme = theme.typography;
  const { dataset: datasetFromRoute } = useParams();
  // Description and Tags show when the caller knows them (the list's Edit
  // details…); the header's Configure keeps editing only the name.
  const showDetails = description !== undefined || tags !== undefined;
  const dataset = datasetId || datasetFromRoute;
  const [openDelete, setOpenDelete] = useState(false);
  // Update and Delete need edit access (W4-5): the server's reason shows on
  // the disabled buttons instead of a refusal after the click.
  const access = useDatasetAccess(dataset);
  const editReason = access.can.edit ? null : access.reason("edit");
  // Delete needs Manage (owner, admin or a Manage grant), not Edit.
  const deleteReason = deleteReasonFor(access.view);
  const [tagInput, setTagInput] = useState("");
  const { control, handleSubmit, reset, watch, setValue } = useForm({
    defaultValues: {
      datasetName,
      description: description ?? "",
      tags: tags ?? [],
    },
    resolver: zodResolver(schema),
  });
  const descriptionValue = watch("description") ?? "";
  const tagValues = watch("tags") ?? [];
  const tagDraft = tagInput.trim().toLowerCase();
  const tagDraftInvalid = Boolean(tagDraft) && !TAG_RE.test(tagDraft);

  const queryClient = useQueryClient();
  const { mutate: updateDataset, isPending } = useMutation({
    mutationFn: (d) => axios.put(endpoints.develop.updateDataset(dataset), d),
    onSuccess: () => {
      enqueueSnackbar("Dataset details saved", { variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["develop", "dataset-list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
      onSaved?.();
      onClose();
    },
    onError: (error) =>
      enqueueSnackbar(
        error?.result || error?.message || "Couldn't save the details.",
        { variant: "error" },
      ),
  });

  const addTag = (raw) => {
    const t = String(raw ?? "")
      .trim()
      .toLowerCase();
    if (!t || !TAG_RE.test(t) || tagValues.includes(t)) return false;
    setValue("tags", [...tagValues, t], { shouldValidate: true });
    setTagInput("");
    return true;
  };

  const handleUpdate = (data) => {
    updateDataset({
      dataset_name: data.datasetName.trim(),
      ...(showDetails && {
        description: data.description.trim(),
        tags: data.tags,
      }),
    });
  };
  const handleDelete = () => {
    setOpenDelete(true);
    onClose();
  };
  return (
    <>
      <Dialog
        open={open}
        onClose={onClose}
        PaperProps={{
          sx: {
            p: theme.spacing(2),
            width: "520px",
          },
        }}
      >
        <DialogTitle
          sx={{
            p: 0,
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <Typography
            variant="m2"
            fontWeight={typographyTheme.fontWeightMedium}
          >
            {title}
          </Typography>
          <IconButton
            aria-label="close-share"
            onClick={onClose}
            sx={{
              padding: 0,
              width: "24px",
              height: "24px",
              display: "flex",
              alignItems: "center",
            }}
          >
            <Iconify icon="line-md:close" />
          </IconButton>
        </DialogTitle>
        <DialogContent
          sx={{
            p: 0,
            pt: 2.5,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <Box sx={{ pt: 1 }}>
            <FormTextFieldV2
              onBlur={() => {}}
              helperText={""}
              defaultValue={datasetName}
              label="Dataset Name"
              size="small"
              control={control}
              placeholder="Enter dataset name"
              fieldName="datasetName"
              isSpinnerField={false}
              disabled={Boolean(editReason)}
              fullWidth
              sx={{
                ":active": {
                  borderWidth: 1,
                },
              }}
            />
          </Box>
          {showDetails && (
            <Controller
              name="description"
              control={control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label="Description"
                  placeholder="What this dataset is for, and where its rows come from"
                  size="small"
                  multiline
                  minRows={3}
                  maxRows={6}
                  disabled={Boolean(editReason)}
                  fullWidth
                  error={Boolean(fieldState.error)}
                  inputProps={{ maxLength: DESCRIPTION_MAX }}
                  helperText={
                    fieldState.error?.message ??
                    `${descriptionValue.length}/${DESCRIPTION_MAX}`
                  }
                  FormHelperTextProps={{ sx: { textAlign: "right", mx: 0 } }}
                />
              )}
            />
          )}
          {showDetails && (
            <Autocomplete
              multiple
              freeSolo
              disabled={Boolean(editReason)}
              options={[]}
              value={tagValues}
              inputValue={tagInput}
              onInputChange={(_, v, reason) => {
                if (reason === "reset") return;
                if (/[,\s]$/.test(v) && addTag(v.replace(/[,\s]+$/, "")))
                  return;
                setTagInput(v);
              }}
              onChange={(_, next) => {
                const clean = [
                  ...new Set(next.map((t) => String(t).trim().toLowerCase())),
                ].filter((t) => TAG_RE.test(t));
                setValue("tags", clean, { shouldValidate: true });
                setTagInput("");
              }}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => (
                  <Chip
                    {...getTagProps({ index })}
                    key={option}
                    label={option}
                    size="small"
                    sx={{
                      height: 22,
                      typography: "s3",
                      bgcolor: "action.hover",
                      color: "text.primary",
                    }}
                  />
                ))
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Tags"
                  size="small"
                  placeholder={tagValues.length ? "" : "golden, support"}
                  error={tagDraftInvalid}
                  helperText={
                    tagDraftInvalid
                      ? TAG_HINT
                      : "Press Enter or comma to add a tag"
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && tagDraft) {
                      e.preventDefault();
                      e.stopPropagation();
                      addTag(tagDraft);
                    }
                  }}
                  onBlur={() => addTag(tagDraft)}
                />
              )}
            />
          )}
          <AccessSection datasetId={dataset} />
        </DialogContent>
        <DialogActions
          sx={{
            p: 0,
            pt: 2,
            justifyContent: "flex-end",
          }}
        >
          <Button
            size="small"
            onClick={() => {
              onClose();
              reset();
            }}
            variant="outlined"
            sx={{
              minWidth: "90px",
            }}
          >
            <Typography variant="s2">Cancel</Typography>
          </Button>
          <Tooltip title={deleteReason ?? ""}>
            <span>
              <Button
                variant="contained"
                onClick={handleDelete}
                disabled={Boolean(deleteReason)}
                size="small"
                startIcon={
                  <SvgColor
                    sx={{
                      width: "16px",
                      height: "16px",
                    }}
                    src="/assets/icons/ic_delete.svg"
                  />
                }
                sx={{
                  bgcolor: "red.500",
                  color: "common.white",
                  minWidth: "90px",
                  ":hover": {
                    bgcolor: "red.700",
                  },
                }}
              >
                <Typography variant="s2">Delete</Typography>
              </Button>
            </span>
          </Tooltip>
          <Tooltip title={editReason ?? ""}>
            <span>
              <Button
                variant="contained"
                size="small"
                disabled={isPending || tagDraftInvalid || Boolean(editReason)}
                onClick={handleSubmit(handleUpdate)}
                sx={{
                  bgcolor: "primary.main",
                  minWidth: "90px",
                  ":hover": {
                    bgcolor: "primary.dark",
                  },
                }}
                startIcon={
                  <Iconify
                    icon="grommet-icons:update"
                    width={14}
                    height={14}
                    sx={{
                      path: {
                        strokeWidth: 1.5,
                      },
                    }}
                  />
                }
              >
                <Typography variant="s2">Update</Typography>
              </Button>
            </span>
          </Tooltip>
        </DialogActions>
      </Dialog>
      <DeleteDataset
        open={openDelete}
        onClose={() => setOpenDelete(false)}
        selected={[{ id: dataset }]}
        redirect
      />
    </>
  );
};

export default ConfigureDatasetModal;

ConfigureDatasetModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetName: PropTypes.string,
  description: PropTypes.string,
  tags: PropTypes.arrayOf(PropTypes.string),
  title: PropTypes.string,
  datasetId: PropTypes.string,
  onSaved: PropTypes.func,
};
