import React, { useMemo, useState } from "react";
import {
  Box,
  Button,
  DialogContent,
  Typography,
  Radio,
  RadioGroup,
  FormControlLabel,
  FormControl,
} from "@mui/material";
import { styled, useTheme } from "@mui/system";
import { useForm } from "react-hook-form";
import PropTypes from "prop-types";
import CustomDialog from "../../Common/CustomDialog/CustomDialog";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { useParams } from "react-router-dom";
import SvgColor from "src/components/svg-color";
import { Icon } from "@iconify/react";
import { ShowComponent } from "src/components/show";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import { useDevelopSelectedRowsStoreShallow } from "../../states";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const StyledButton = styled(Button)(({ theme }) => ({
  color: theme.palette.text.primary,
  fontWeight: theme.typography["fontWeightRegular"],
  ...theme.typography["s1"],
}));

const StyledFormControl = styled(FormControl)({
  width: "100%",
});

const DataMenuList = ({
  setName,
  onCreateDatasetRows,
  onMergeRows,
  setTargetDatasetId,
  loading,
}) => {
  const [openDialog, setOpenDialog] = useState(false);
  const [selectedOption, setSelectedOption] = useState("new");
  const theme = useTheme();
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const { selectAll, toggledNodes } = useDevelopSelectedRowsStoreShallow(
    (s) => ({
      selectAll: s.selectAll,
      toggledNodes: s.toggledNodes,
      resetSelectedRows: s.resetSelectedRows,
    }),
  );

  const { control, watch, reset } = useForm({
    defaultValues: {
      datasetName: "",
    },
  });

  const watchedDatasetName = watch("datasetName");

  const { data: datasetList } = useDevelopDatasetList();
  const { dataset } = useParams();
  const { data } = useDevelopDatasetList();
  const { role } = useAuthContext();

  const currentDataset = data?.find((d) => d.datasetId === dataset)?.name;

  const datasetOptions = useMemo(() => {
    if (!datasetList) return [];

    return datasetList.map((dataset) => ({
      value: dataset.datasetId,
      label: dataset.name,
    }));
  }, [datasetList]);

  const handleButtonClick = () => {
    trackEvent(Events.addRowToDatasetClicked);
    setOpenDialog(true);
  };

  const handleClose = () => {
    setOpenDialog(false);
    setSelectedOption("new");
    setSelectedDatasetId("");
    reset(); // Reset form values
  };

  const handleOptionChange = (event) => {
    setSelectedOption(event.target.value);
    if (event.target.value === "new") {
      setSelectedDatasetId("");
    } else {
      reset();
    }
  };

  const handleSelect = (datasetId) => {
    setSelectedDatasetId(datasetId);
  };

  const styles = useMemo(() => {
    return {
      dialogRoot: {
        display: "flex",
        flexDirection: "column",
        gap: theme.spacing(2),
      },
      radioGroupWrapper: {
        paddingLeft: theme.spacing(1),
      },
      radioGroup: {
        gap: theme.spacing(2),
        marginTop: theme.spacing(1),
      },
      radioLabel: {
        "& .MuiFormControlLabel-label": {
          color: "text.secondary",
          fontWeight: "fontWeightRegular",
        },
      },
      radio: {
        color: "action.selected",
        "& .Mui-checked": {
          color: "primary.light",
        },
      },
      textField: {
        width: "100%",
        "& .MuiOutlinedInput-notchedOutline legend": {
          width: `11ch`,
        },
        "& .MuiInputBase-input": {
          color: "text.primary",
        },
        "& .MuiInputBase-input::placeholder": {
          color: "text.disabled",
        },
      },
      dropdown: {
        maxWidth: "438px",
        width: "480px",
        "& .MuiInputBase-root": {
          height: 41,
          padding: theme.spacing(0, 1.5),
          display: "flex",
          alignItems: "center",
        },
        "& .MuiSelect-select": {
          padding: 0,
          display: "flex",
          alignItems: "center",
        },
      },
    };
  }, [theme]);

  const handleAction = () => {
    if (selectedOption === "new") {
      setName(watchedDatasetName);
      trackEvent(Events.addRowToDatasetSuccessful, {
        [PropertyName.method]: "new dataset",
      });
      onCreateDatasetRows(watchedDatasetName);
    } else {
      setTargetDatasetId(selectedDatasetId);
      const selectedDataset = datasetList?.find(
        (d) => d.datasetId === selectedDatasetId,
      );

      trackEvent(Events.addRowToExistingDatasetSuccessful, {
        [PropertyName.rowToExistingDataset]: {
          original_dataset_name: currentDataset,
          Existing_dataset_name: selectedDataset?.name,
          row_id: toggledNodes,
          select_all: selectAll,
        },
      });
      onMergeRows(selectedDatasetId);
    }
    handleClose();
  };

  const isActionDisabled = () => {
    if (selectedOption === "new") {
      return !watchedDatasetName?.trim();
    } else {
      return !selectedDatasetId;
    }
  };

  return (
    <div>
      <StyledButton
        variant="text"
        size="small"
        startIcon={
          <SvgColor
            src="/assets/icons/action_buttons/ic_add.svg"
            sx={{ width: 20, height: 20, color: "text.secondary" }}
          />
        }
        onClick={handleButtonClick}
        disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
      >
        Add to dataset
      </StyledButton>

      <CustomDialog
        title="Add to Dataset"
        actionButton={
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Icon icon="mdi:plus" width={18} height={18} color="inherit" />
            <Typography
              fontWeight={400}
              fontSize={14}
              color={"background.neutral"}
            >
              {selectedOption === "new" ? "Add Dataset" : "Add to Dataset"}
            </Typography>
          </Box>
        }
        open={openDialog}
        loading={loading}
        onClose={handleClose}
        onClickAction={handleAction}
        isData={!isActionDisabled()}
      >
        <DialogContent sx={{ padding: 0, margin: 0 }}>
          <Box sx={styles.dialogRoot}>
            <Box sx={styles.radioGroupWrapper}>
              <FormControl component="fieldset">
                <RadioGroup
                  value={selectedOption}
                  onChange={handleOptionChange}
                  row
                  sx={styles.radioGroup}
                >
                  <FormControlLabel
                    value="new"
                    control={<Radio />}
                    label="Add to new dataset"
                    sx={styles.radioLabel}
                  />
                  <FormControlLabel
                    value="existing"
                    control={<Radio />}
                    label="Add to existing dataset"
                    sx={styles.radioLabel}
                  />
                </RadioGroup>
              </FormControl>
            </Box>

            <Box>
              <ShowComponent condition={selectedOption === "new"}>
                <FormTextFieldV2
                  control={control}
                  fieldName="datasetName"
                  defaultValue=""
                  label="Dataset Name"
                  fieldType="text"
                  placeholder="Enter Dataset name"
                  size="small"
                  sx={styles.textField}
                />
              </ShowComponent>
              <ShowComponent condition={selectedOption === "existing"}>
                <StyledFormControl>
                  <FormSearchSelectFieldState
                    value={selectedDatasetId}
                    options={datasetOptions}
                    label={"Dataset"}
                    onChange={(e) => {
                      handleSelect(e.target.value);
                    }}
                    placeholder="Choose an existing dataset"
                    size="small"
                  />
                </StyledFormControl>
              </ShowComponent>
            </Box>
          </Box>
        </DialogContent>
      </CustomDialog>
    </div>
  );
};

DataMenuList.propTypes = {
  setName: PropTypes.func,
  onCreateDatasetRows: PropTypes.func,
  loading: PropTypes.bool,
  onMergeRows: PropTypes.func,
  setTargetDatasetId: PropTypes.func,
};

export default DataMenuList;
