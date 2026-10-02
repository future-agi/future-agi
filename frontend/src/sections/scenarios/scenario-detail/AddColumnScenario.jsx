import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { ColumnCard } from "./ColumnCard";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import RadioField from "src/components/RadioField/RadioField";
import SvgColor from "src/components/svg-color";
import { enqueueSnackbar } from "notistack";
import { LoadingButton } from "@mui/lab";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useRefreshScenarioGrid } from "./useRefreshScenarioGrid";
import { addColumnSchema, columnGenerationOptions } from "./common";

const AddColumnScenario = ({ open, onClose, datasetId, scenarioId }) => {
  const refreshGrid = useRefreshScenarioGrid(scenarioId);

  const addColumnForm = useForm({
    mode: "onSubmit",
    resolver: zodResolver(addColumnSchema),
    defaultValues: {
      mode: "manual",
      columns: [
        {
          name: "",
          type: "",
          description: "",
        },
      ],
    },
  });

  const handleClose = () => {
    addColumnForm.reset();
    onClose();
  };

  const { mutate: addManualColumn, isPending: isAddingManCols } = useMutation({
    /**
     * @param {object} d
     */
    mutationFn: (d) =>
      axios.post(endpoints.develop.addMultipleColumns(datasetId), d),
    onSuccess: () => {
      enqueueSnackbar("Columns added successfully", { variant: "success" });
      refreshGrid();
      handleClose();
    },
  });

  const { mutate: addAIColumns, isPending: isAddingCols } = useMutation({
    /**
     * @param {object} data
     */
    mutationFn: (data) =>
      axios.post(endpoints.scenarios.addCols(scenarioId), data),
    onSuccess: () => {
      enqueueSnackbar("Columns added successfully");
      handleClose();
      refreshGrid();
    },
  });

  const {
    control,
    handleSubmit,
    formState: { errors },
  } = addColumnForm;

  const columns = useWatch({
    control,
    name: "columns",
  });

  const { fields, remove, append } = useFieldArray({
    control,
    name: "columns",
  });

  const addColumn = () => {
    if (columns.length >= 10) {
      enqueueSnackbar("Max 10 columns can be added at time", {
        variant: "warning",
      });
      return;
    }

    append({
      name: "",
      type: "",
      description: "",
      isDelete: true,
      isOpen: true,
    });
  };

  const removeColumn = (index) => {
    if (columns.length === 1) return;
    remove(index);
  };

  const onSubmit = async (formData) => {
    if (!scenarioId) return;

    const { mode, columns = [] } = formData;

    const transformedColumns = columns.map((col) => ({
      name: col?.name,
      data_type: col?.type,
      description: col?.description,
    }));

    if (mode === "manual") {
      addManualColumn({
        columns: transformedColumns?.map((col) => ({
          new_column_name: col?.name,
          column_type: col?.data_type,
          description: col?.description,
        })),
      });
    } else {
      addAIColumns({ columns: transformedColumns });
    }
  };

  const formLoading = isAddingManCols || isAddingCols;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      disableEscapeKeyDown={formLoading}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          width: "676px",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <Box sx={{ padding: 2, paddingBottom: 0 }}>
          <IconButton
            onClick={handleClose}
            sx={{ position: "absolute", top: "10px", right: "12px" }}
          >
            <Iconify icon="mingcute:close-line" color="text.primary" />
          </IconButton>

          <Box sx={{ display: "flex", flexDirection: "column" }}>
            <Typography
              variant="m2"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              Add Columns
            </Typography>
            <Typography
              variant="s1"
              fontWeight={"fontWeightRegular"}
              color={"text.secondary"}
            >
              Define the column name, type, and description (max 10 columns)
            </Typography>
          </Box>
        </Box>

        <Divider sx={{ mt: 2 }} />

        <Box
          sx={{
            flex: 1,
            overflowY: "auto",
            padding: 2,
            paddingBottom: 10,
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column" }}>
            <Typography
              variant="m3"
              fontWeight={"fontWeightSemiBold"}
              marginBottom={1}
            >
              Select how you want to add data
            </Typography>

            <RadioField
              options={columnGenerationOptions}
              control={control}
              fieldName={"mode"}
              optionColor={"text.primary"}
              groupSx={{ padding: 0, marginLeft: -1 }}
              other={{
                radioSx: { color: "text.primary" },
              }}
            />
          </Box>

          <Box
            sx={{
              width: "100%",
              marginTop: 4,
              display: "flex",
              flexDirection: "column",
              gap: 2,
            }}
          >
            {fields.map((field, index) => {
              return (
                <ColumnCard
                  removable={fields?.length > 1}
                  removeColumn={removeColumn}
                  key={field.id}
                  control={control}
                  index={index}
                  ColumnError={errors?.columns?.[index] ?? false}
                />
              );
            })}
          </Box>

          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "flex-start",
              marginTop: 2,
            }}
          >
            <Button
              sx={{
                width: "auto",
                color: "primary.main",
                borderColor: "primary.main",
                border: "1px solid",
                borderRadius: 0.5,
                "&:hover": {
                  borderColor: "primary.main",
                },
              }}
              variant="outlined"
              onClick={addColumn}
              disabled={formLoading}
              startIcon={
                <SvgColor
                  src="/assets/icons/ic_add.svg"
                  width={15}
                  height={15}
                  sx={{ mr: 1 }}
                />
              }
            >
              Add Column
            </Button>
          </Box>
        </Box>

        <Box
          sx={{
            position: "sticky",
            bottom: 0,
            left: 0,
            width: "100%",
            display: "flex",
            alignItems: "center",
            gap: 1,
            padding: 2,
            bgcolor: "background.paper",
            borderTop: "1px solid",
            borderColor: "divider",
            zIndex: 1300,
          }}
        >
          <Button
            disabled={formLoading}
            variant="outlined"
            onClick={handleClose}
            sx={{ flex: 1 }}
          >
            Cancel
          </Button>
          <LoadingButton
            variant="contained"
            color="primary"
            sx={{ flex: 1 }}
            onClick={handleSubmit(onSubmit)}
            loading={formLoading}
          >
            Add
          </LoadingButton>
        </Box>
      </Box>
    </Drawer>
  );
};

export default AddColumnScenario;

AddColumnScenario.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  scenarioType: PropTypes.string,
  scenarioId: PropTypes.string,
};
