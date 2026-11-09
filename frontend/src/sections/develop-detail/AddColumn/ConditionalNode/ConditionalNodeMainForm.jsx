import {
  Box,
  Button,
  FormHelperText,
  IconButton,
  MenuItem,
  Select,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { LoadingButton } from "@mui/lab";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import ConditionalInput from "./ConditionalInput";
import { useParams } from "react-router";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import {
  Controller,
  useFieldArray,
  useFormContext,
  useWatch,
} from "react-hook-form";
import { ShowComponent } from "../../../../components/show";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useConditionalNodeStoreShallow } from "../../states";
import { enqueueSnackbar } from "src/components/snackbar";
import { isPrototypeMode } from "src/utils/prototype";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { EXECUTE_CODE_COLUMN_OFFERED } from "../common";
import RerunScopeField from "../RerunScopeField";
import { withRerunScope } from "../rerunScope";
import { rerunResultToast } from "../rerunResult";

const COLUMN_TYPE_OPTIONS = [
  { label: "Static value", value: "static_value" },
  { label: "Run Prompt", value: "run_prompt" },
  { label: "Retrieval", value: "retrieval" },
  { label: "Extract Entities", value: "extract_entities" },
  { label: "Extract JSON Key", value: "extract_json" },
  ...(EXECUTE_CODE_COLUMN_OFFERED
    ? [{ label: "Execute Custom Code", value: "extract_code" }]
    : []),
  { label: "Classification", value: "classification" },
  { label: "API Calls", value: "api_call" },
];

const ConditionalNodeBranch = ({ index, setOpenForm, handleDeleteBranch }) => {
  const { dataset } = useParams();
  const allColumns = useDatasetColumnConfig(dataset);
  const {
    control,
    setValue,
    formState: { errors },
  } = useFormContext();
  const existingBranchType = useWatch({
    control,
    name: `config.${index}.branchNodeConfig.type`,
  });

  const existingConditionType = useWatch({
    control,
    name: `config.${index}.branchType`,
  });

  const handleColumnTypeChange = (e, index) => {
    if (existingBranchType !== e.target.value || !e.target.value) {
      // reset the config
      setValue(`config.${index}.branchNodeConfig.config`, null);
    }
    if (!e.target.value) return;
    // A static value is typed in place (no sub-form).
    if (e.target.value === "static_value") {
      setValue(`config.${index}.branchNodeConfig.config`, { value: "" });
      return;
    }

    setOpenForm({ index, formType: e.target.value });
  };

  const handleEdit = () => {
    if (existingBranchType === "static_value") return;
    setOpenForm({ index, formType: existingBranchType });
  };

  return (
    <Box
      sx={{
        backgroundColor: "divider",
        border: "1px solid var(--border-default)",
        borderRadius: "8px",
        marginBottom: "16px",
        width: "100%",
        padding: "20px",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "16px",
        }}
      >
        {index === 0 ? (
          <Typography fontSize="14px" fontWeight={600}>
            If
          </Typography>
        ) : (
          <Controller
            name={`config.${index}.branchType`}
            control={control}
            render={({ field: { onChange, value } }) => (
              <Select
                size="small"
                value={value}
                onChange={onChange}
                sx={{ minWidth: 100 }}
              >
                <MenuItem value="elif">
                  <strong>Elif</strong>
                </MenuItem>
                <MenuItem value="else">
                  <strong>Else</strong>
                </MenuItem>
              </Select>
            )}
          />
        )}
        <Box sx={{ display: "flex", gap: 1 }}>
          <IconButton
            size="small"
            onClick={() => handleEdit()}
            color="default"
            disabled={
              !existingBranchType || existingBranchType === "static_value"
            }
          >
            <Iconify icon="solar:pen-bold" />
          </IconButton>
          <ShowComponent condition={index !== 0}>
            <IconButton size="small" onClick={() => handleDeleteBranch(index)}>
              <Iconify icon="solar:trash-bin-trash-bold" />
            </IconButton>
          </ShowComponent>
        </Box>
      </Box>

      {existingConditionType !== "else" && (
        <ConditionalInput
          control={control}
          fieldName={`config.${index}.condition`}
          allColumns={allColumns}
        />
      )}
      <Box sx={{ mt: 2 }}>
        <FormSearchSelectFieldControl
          fullWidth
          label="Select Column Type"
          size="small"
          control={control}
          fieldName={`config.${index}.branchNodeConfig.type`}
          options={COLUMN_TYPE_OPTIONS}
          onChange={(event) => handleColumnTypeChange(event, index)}
        />
      </Box>
      {existingBranchType === "static_value" && (
        <Box sx={{ mt: 2 }}>
          <FormTextFieldV2
            label="Value"
            size="small"
            control={control}
            fieldName={`config.${index}.branchNodeConfig.config.value`}
            // L2: dev keeps a static value as typed (no {{column}} fill).
            placeholder={
              isPrototypeMode() ? "e.g. n/a, or {{column}}" : "e.g. n/a"
            }
            fullWidth
          />
        </Box>
      )}
      <ShowComponent
        condition={errors?.config?.[index]?.branchNodeConfig?.config}
      >
        <FormHelperText error={true}>
          Please complete configuration for this branch
        </FormHelperText>
      </ShowComponent>
    </Box>
  );
};

ConditionalNodeBranch.propTypes = {
  index: PropTypes.number,
  field: PropTypes.object,
  setOpenForm: PropTypes.any,
  handleDeleteBranch: PropTypes.any,
};

const ConditionalNodeMainForm = ({
  editId,
  setOpenForm,
  preview,
  isPreviewPending,
}) => {
  const setOpenConditionalNode = useConditionalNodeStoreShallow(
    (s) => s.setOpenConditionalNode,
  );
  const { refreshGrid } = useDevelopDetailContext();

  const onClose = () => {
    setOpenConditionalNode(false);
  };
  const { control, handleSubmit, reset } = useFormContext();
  const { fields, append, remove } = useFieldArray({
    control,
    name: "config",
  });
  const { dataset } = useParams();

  const { mutate: addColumn, isPending: isSubmitting } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.develop.addColumns.conditionalnode(dataset), data),
    onSuccess: () => {
      enqueueSnackbar("Conditional column created successfully", {
        variant: "success",
      });
      refreshGrid(null, true);
      onClose();
    },
  });

  const { mutate: updateColumn, isPending: isUpdating } = useMutation({
    mutationFn: (data) =>
      axios.post(
        endpoints.develop.addColumns.updateDynamicColumn(editId),
        data,
      ),
    onSuccess: (res) => {
      const toast = rerunResultToast("Conditional Node", res);
      enqueueSnackbar(toast.text, { variant: toast.variant });
      refreshGrid();
      onClose();
    },
  });

  const transformFormToApi = (formValues) => {
    const { newColumnName, config, ...rest } = formValues;
    return {
      ...rest,
      config: config?.map(
        ({ branchType, branchNodeConfig, ...branchRest }) => ({
          ...branchRest,
          branch_type: branchType,
          branch_node_config: branchNodeConfig,
        }),
      ),
      ...(newColumnName && { new_column_name: newColumnName }),
    };
  };

  const [rerunScope, setRerunScope] = React.useState("all");
  const onSubmit = (formValues) => {
    if (editId) {
      updateColumn(
        withRerunScope(
          {
            config: { ...transformFormToApi(formValues) },
            operation_type: "conditional",
          },
          rerunScope,
        ),
      );
      return;
    }

    addColumn(transformFormToApi(formValues));
  };

  const handleAddBranch = () => {
    // L7b G6: the new branch is added without focusing its first field, so
    // its Select Column Type list does not open by itself.
    append(
      {
        branchType: "elif",
        condition: "",
        branchNodeConfig: { type: "", config: null },
      },
      { shouldFocus: false },
    );
  };

  // L7b G6: typing in the form (a condition with "c", a value) stays in the
  // form: the Data grid's single-key shortcuts never see it. Escape still
  // reaches the drawer so it closes.
  const keepKeysInForm = (e) => {
    if (e.key !== "Escape") e.stopPropagation();
  };

  const handleDeleteBranch = (index) => {
    remove(index);
  };

  const onCloseClick = () => {
    reset();
    onClose();
  };

  return (
    <Box
      sx={{
        padding: "20px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        width: "550px",
      }}
      component="form"
      onKeyDown={keepKeysInForm}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography fontWeight={700} color="text.secondary">
          {editId ? "Edit Conditional Node" : "Conditional Node"}
        </Typography>
        <IconButton onClick={onCloseClick} size="small">
          <Iconify icon="mingcute:close-line" />
        </IconButton>
      </Box>
      <Box
        sx={{
          gap: 2,
          display: "flex",
          flexDirection: "column",
          flex: 1,
          overflow: "auto",
          paddingTop: 1,
        }}
      >
        <ShowComponent condition={!editId}>
          <FormTextFieldV2
            label="Name"
            size="small"
            control={control}
            placeholder="Enter column name"
            fieldName="newColumnName"
          />
        </ShowComponent>

        {fields.map((field, index) => (
          <ConditionalNodeBranch
            key={field.id}
            index={index}
            field={field}
            control={control}
            setOpenForm={setOpenForm}
            handleDeleteBranch={handleDeleteBranch}
          />
        ))}

        <Button fullWidth variant="outlined" onClick={handleAddBranch}>
          Add Branch
        </Button>
        {editId && (
          <RerunScopeField value={rerunScope} onChange={setRerunScope} />
        )}
      </Box>

      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        <LoadingButton
          onClick={handleSubmit(preview)}
          variant="outlined"
          fullWidth
          size="small"
          loading={isPreviewPending}
        >
          Test
        </LoadingButton>
        <LoadingButton
          variant="contained"
          color="primary"
          fullWidth
          size="small"
          loading={isSubmitting || isUpdating}
          onClick={handleSubmit(onSubmit)}
        >
          {editId ? "Update Column" : "Create New Column"}
        </LoadingButton>
      </Box>
    </Box>
  );
};

ConditionalNodeMainForm.propTypes = {
  editId: PropTypes.string,
  setOpenForm: PropTypes.any,
  preview: PropTypes.func,
  isPreviewPending: PropTypes.bool,
};

export default ConditionalNodeMainForm;
