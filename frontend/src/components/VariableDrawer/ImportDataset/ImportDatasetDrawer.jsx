import React, { useCallback } from "react";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Skeleton,
  Typography,
  useTheme,
} from "@mui/material";
import { useMemo } from "react";
import {
  useDevelopDatasetList,
  useGetDatasetDetail,
} from "src/api/develop/develop-detail";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import FieldSelection from "src/sections/develop-detail/Common/FieldSelection";
import { ShowComponent } from "src/components/show";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { usePromptWorkbenchContext } from "src/sections/workbench/createPrompt/WorkbenchContext";
import {
  buildImportColumns,
  buildVariableData,
  getMappingValue,
} from "./utils";

const ImportDatasetDrawerChild = ({ onClose, variables, setVariableData }) => {
  const { setValuesChanged } = usePromptWorkbenchContext();

  const theme = useTheme();

  // Helper function to check if a variable has a mapping
  const hasVariableMapping = useCallback((mapping, variablePath) => {
    const value = getMappingValue(mapping, variablePath);
    return Boolean(value);
  }, []);

  const methods = useForm({
    defaultValues: {
      config: {
        mapping: {},
      },
      dataset: "",
    },
  });
  const { control, handleSubmit, watch, setValue } = methods;

  const datasetValue = watch("dataset");

  const handleDatasetChange = (e) => {
    setValue("dataset", e.target.value);
    setValue("config.mapping", {});
  };

  const mapping = useWatch({
    control,
    name: "config.mapping",
  });

  // Check if at least one mapping is selected and has a value
  const isFormValid = useMemo(() => {
    return variables.some((variable) => hasVariableMapping(mapping, variable));
  }, [hasVariableMapping, mapping, variables]);

  // Count of mapped columns that will be imported
  const mappedColumnsCount = useMemo(() => {
    return variables.filter((variable) => hasVariableMapping(mapping, variable))
      .length;
  }, [hasVariableMapping, mapping, variables]);

  const { data: datasetList = [] } = useDevelopDatasetList("");

  const datasetOptions = useMemo(
    () =>
      datasetList?.map((item) => ({
        label: item.name,
        value: item.id || item.datasetId,
      })),
    [datasetList],
  );

  const selectedDataset = useMemo(() => {
    return datasetOptions.find((opt) => opt.value === datasetValue);
  }, [datasetValue, datasetOptions]);

  const { data: datasetDetail, isPending: isLoadingDatasetDetail } =
    useGetDatasetDetail(selectedDataset?.value, {
      enabled: Boolean(selectedDataset?.value),
    });

  const onSubmit = (data) => {
    const variableData = buildVariableData(
      data.config.mapping,
      datasetDetail,
      variables,
    );

    setValuesChanged(true);
    setVariableData(variableData);
    onClose?.();
  };

  const transformedColumns = useMemo(
    () => buildImportColumns(datasetDetail),
    [datasetDetail],
  );

  const isColumnEmpty = transformedColumns?.length > 0;

  return (
    <FormProvider {...methods}>
      <form onSubmit={handleSubmit(onSubmit)}>
        <Box
          width={600}
          height="100vh"
          display="flex"
          gap={2}
          flexDirection="column"
          pt={theme.spacing(2)}
          px={theme.spacing(2)}
        >
          <Box
            display="flex"
            justifyContent="space-between"
            alignItems="center"
          >
            <Typography fontSize={16} fontWeight={600} color={"text.primary"}>
              Import from Dataset
            </Typography>
            <IconButton onClick={onClose} sx={{ p: 0 }}>
              <Iconify
                icon="mingcute:close-line"
                color="text.primary"
                width="24px"
              />
            </IconButton>
          </Box>

          <Box>
            <FormSearchSelectFieldControl
              control={control}
              fieldName="dataset"
              label="Dataset"
              size="small"
              placeholder="Select a dataset"
              options={datasetOptions}
              fullWidth
              onChange={handleDatasetChange}
              error={datasetValue && !isColumnEmpty && !isLoadingDatasetDetail}
              helperText={
                datasetValue && !isColumnEmpty && !isLoadingDatasetDetail
                  ? "Dataset doesn't have any column"
                  : undefined
              }
            />
          </Box>

          {/* Warning message - always visible */}
          <Box
            sx={{
              padding: theme.spacing(1.5),
              backgroundColor: "background.default",
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box display="flex" alignItems="center" gap={1} mb={0.5}>
              <Iconify
                icon="solar:info-circle-bold"
                color="text.secondary"
                width="16px"
              />
              <Typography
                variant="caption"
                color="text.secondary"
                fontWeight={500}
              >
                Import Information
              </Typography>
            </Box>
            <Typography variant="caption" color="text.secondary">
              • Existing variable data will be overwritten
            </Typography>
            <br />
            <Typography variant="caption" color="text.secondary">
              • Only mapped columns will be imported ({mappedColumnsCount}{" "}
              column{mappedColumnsCount !== 1 ? "s" : ""} selected)
            </Typography>
          </Box>

          <Box
            flex={1}
            overflow="auto"
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              scrollbarWidth: "none",
              msOverflowStyle: "none",
              "&::-webkit-scrollbar": {
                display: "none",
              },
            }}
          >
            <Box sx={{ p: theme.spacing(2), pt: theme.spacing(1) }}>
              <Typography fontSize={14} fontWeight={600} color={"text.primary"}>
                Map columns to variable
              </Typography>
              <ShowComponent condition={datasetValue && isLoadingDatasetDetail}>
                {[...Array(variables.length)].map((_, idx) => (
                  <Box
                    key={idx}
                    display="flex"
                    alignItems="center"
                    width="100%"
                    gap={theme.spacing(2)}
                    mb={theme.spacing(2)}
                    mt={theme.spacing(2)}
                  >
                    <Skeleton
                      variant="rectangular"
                      height={40}
                      sx={{ borderRadius: 1, flex: 2 }}
                    />
                    <Skeleton height={8} sx={{ flex: 1 }} />
                    <Skeleton
                      variant="rectangular"
                      height={40}
                      sx={{ borderRadius: 1, flex: 2 }}
                    />
                  </Box>
                ))}
              </ShowComponent>
              <ShowComponent
                condition={datasetValue && !isLoadingDatasetDetail}
              >
                {variables.map((variable, index) => (
                  <Box mt={2} key={`${variable}-${index}`}>
                    <FieldSelection
                      field={variable}
                      defaultvalue={null}
                      fieldName={`config.mapping.${variable}`}
                      allColumns={transformedColumns}
                      control={control}
                      fullWidth
                      isMultipleColumn={false}
                      check={false}
                      isChecked={false}
                      handleCheckbox={undefined}
                      placeholder="Select a column"
                    />
                  </Box>
                ))}
              </ShowComponent>
            </Box>
          </Box>

          <Box
            bgcolor="background.paper"
            pt={theme.spacing(2)}
            pb={theme.spacing(1)}
            display="flex"
            justifyContent="flex-end"
          >
            <Button
              type="submit"
              disabled={!isFormValid}
              variant="contained"
              color="primary"
              sx={{ px: theme.spacing(8) }}
            >
              Apply
            </Button>
          </Box>
        </Box>
      </form>
    </FormProvider>
  );
};

ImportDatasetDrawerChild.propTypes = {
  onClose: PropTypes.func,
  variables: PropTypes.array,
  setVariableData: PropTypes.func,
};

const ImportDatasetDrawer = ({ open, onClose, variables, setVariableData }) => {
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <ImportDatasetDrawerChild
        onClose={onClose}
        variables={variables}
        setVariableData={setVariableData}
      />
    </Drawer>
  );
};

ImportDatasetDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  variables: PropTypes.array,
  setVariableData: PropTypes.func,
};

export default ImportDatasetDrawer;
