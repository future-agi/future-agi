import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import Iconify from "src/components/iconify";
import {
  AnnotationFormDefaultSettings,
  AnnotationTypes,
  getDefaultCreateLabelFormValues,
} from "./common";
import ConditionalLabelForm from "./ConditionalLabelForm";
import LabelPreview from "./LabelPreview";
import { zodResolver } from "@hookform/resolvers/zod";
import { createCreateEditLabelValidationSchema } from "./validation";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

// Backend serializer (`no_of_stars`, `max_length`, etc.) used to be fed by
// djangorestframework-camel-case. That package was removed, so the frontend
// must now send snake_case directly. Convert the known camelCase settings
// keys at the mutation boundary; pass everything else through untouched.
const SETTINGS_KEY_MAP = {
  maxLength: "max_length",
  minLength: "min_length",
  stepSize: "step_size",
  displayType: "display_type",
  multiChoice: "multi_choice",
  noOfStars: "no_of_stars",
};

const toSnakeLabelSettings = (settings) => {
  if (!settings || typeof settings !== "object") return settings;
  const out = {};
  for (const [key, value] of Object.entries(settings)) {
    out[SETTINGS_KEY_MAP[key] ?? key] = value;
  }
  return out;
};

const CreateEditLabelChild = ({ projectId, onClose, onSuccess, editData }) => {
  const {
    control,
    setValue,
    handleSubmit,
    formState: { isValid, isDirty },
  } = useForm({
    defaultValues: getDefaultCreateLabelFormValues(editData),
    resolver: zodResolver(createCreateEditLabelValidationSchema(editData)),
    mode: "onChange",
  });
  const theme = useTheme();

  const onAnnotationTypeChange = (e) => {
    const type = e.target.value;
    const defaultSettings = AnnotationFormDefaultSettings[type];
    setValue("settings", defaultSettings);
  };
  const { mutate: createLabel, isPending: isCreatingLabel } = useMutation({
    mutationFn: (data) => {
      let apiData = {
        ...data,
        project: projectId,
      };
      if (data.type === "categorical") {
        // some extra non form data needed to be sent for simple categorical
        apiData = {
          ...apiData,
          settings: {
            ...apiData.settings,
            rule_prompt: "",
            auto_annotate: false,
            strategy: null,
          },
        };
      }
      apiData = {
        ...apiData,
        settings: toSnakeLabelSettings(apiData.settings),
      };
      return axios.post(endpoints.project.createLabel(), apiData);
    },
    onSuccess: (...args) => {
      onSuccess(...args);
      onClose();
      enqueueSnackbar("Label created successfully", { variant: "success" });
    },
  });

  const { mutate: editLabel } = useMutation({
    mutationFn: (data) => {
      let apiData = {
        ...data,
        project: projectId,
      };
      if (data.type === "categorical") {
        // some extra non form data needed to be sent for simple categorical
        apiData = {
          ...apiData,
          settings: {
            ...apiData.settings,
            rule_prompt: "",
            auto_annotate: false,
            strategy: null,
          },
        };
      }
      apiData = {
        ...apiData,
        settings: toSnakeLabelSettings(apiData.settings),
      };
      return axios.put(endpoints.project.updateLabel(editData.id), apiData);
    },
    onSuccess: (...args) => {
      onSuccess(...args);
      onClose();
      enqueueSnackbar("Label updated successfully", { variant: "success" });
    },
    onError: (e) => {
      enqueueSnackbar(e?.message || "Failed to update label", {
        variant: "error",
      });
    },
  });

  const onSubmit = (data) => {
    if (editData) {
      editLabel(data);
    } else {
      createLabel(data);
    }
  };

  return (
    <>
      <form onSubmit={handleSubmit(onSubmit)}>
        <DialogContent
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(2),
            pt: theme.spacing(2),
            px: theme.spacing(2),
          }}
        >
          <FormTextFieldV2
            label="Label Name"
            control={control}
            fieldName="name"
            size="small"
            fullWidth
            required
            placeholder="Enter label name"
          />
          <FormSearchSelectFieldControl
            control={control}
            fieldName="type"
            size="small"
            label="Annotation Type"
            required={true}
            disabled={Boolean(editData)}
            options={AnnotationTypes}
            fullWidth
            onChange={onAnnotationTypeChange}
          />
          <Box sx={{ display: "flex", flexDirection: "column" }}>
            <Typography
              fontSize={13}
              fontWeight={500}
              color={theme.palette.text.secondary}
            >
              Description
              {/* <Typography component="span" color="error.main">
                *
              </Typography> */}
            </Typography>
            <FormTextFieldV2
              control={control}
              fieldName="description"
              size="small"
              multiline
              rows={2}
              placeholder="Enter description here"
              sx={{
                fontSize: "16px",
                "& .MuiOutlinedInput-root": {
                  backgroundColor: theme.palette.background.neutral,
                  paddingX: theme.spacing(2),
                  paddingY: theme.spacing(1.5),
                  "& input": {
                    fontSize: theme.spacing(1.5),
                  },
                },
              }}
            />
          </Box>
          <ConditionalLabelForm control={control} />
          <LabelPreview control={control} />
        </DialogContent>
        <DialogActions sx={{ py: theme.spacing(2) }}>
          <Button
            aria-label="close-edit-annotation"
            size="small"
            fullWidth
            variant="outlined"
            sx={{
              color: theme.palette.text.secondary,
            }}
            onClick={onClose}
          >
            Cancel
          </Button>
          <LoadingButton
            aria-label="save-edited-annotation"
            size="small"
            fullWidth
            variant="contained"
            color="primary"
            type="submit"
            disabled={!isValid || !isDirty}
            loading={isCreatingLabel}
          >
            {editData ? "Update" : "Save"}
          </LoadingButton>
        </DialogActions>
      </form>
    </>
  );
};

CreateEditLabelChild.propTypes = {
  projectId: PropTypes.string,
  onClose: PropTypes.func,
  onSuccess: PropTypes.func,
  editData: PropTypes.object,
};

const CreateEditLabel = ({ open, onClose, projectId, onSuccess, editData }) => {
  const theme = useTheme();
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          p: theme.spacing(2),
          pb: theme.spacing(0),
          color: theme.palette.text.primary,
        }}
      >
        <Typography fontWeight={600} variant="m3">
          {editData ? "Edit Label" : "New Label"}
        </Typography>
        <IconButton
          aria-label="close-edit"
          onClick={onClose}
          sx={{ p: theme.spacing(0) }}
        >
          <Iconify
            icon="solar:close-linear"
            width={20}
            color={theme.palette.text.primary}
          />
        </IconButton>
      </DialogTitle>
      <CreateEditLabelChild
        projectId={projectId}
        onClose={onClose}
        onSuccess={onSuccess}
        editData={editData}
      />
    </Dialog>
  );
};

CreateEditLabel.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  projectId: PropTypes.string,
  onSuccess: PropTypes.func,
  editData: PropTypes.object,
};

export default CreateEditLabel;
