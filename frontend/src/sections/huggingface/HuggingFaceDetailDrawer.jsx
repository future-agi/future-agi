import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { Drawer, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import HuggingDetailForm from "./HuggingDetailForm";

const HuggingFaceDetailDrawer = ({
  show,
  reset,
  control,
  huggingFaceDetail,
  watch,
  subsetOptions,
  splitOptions,
  onSubmit,
  onClose,
  isLoadingCreateDataset,
  showNameField,
  huggingFaceDatasetConfigError,
}) => {
  useEffect(() => {
    if (!show) return;

    const currentValues = watch();
    const valuesToReset = {};

    if (showNameField && huggingFaceDetail?.name && !currentValues.name) {
      valuesToReset.name = huggingFaceDetail.name;
    }

    const selectedSubset = currentValues.huggingface_dataset_config;
    const hasSelectedSubset = subsetOptions?.some(
      ({ value }) => value === selectedSubset,
    );
    if (subsetOptions?.length > 0 && !hasSelectedSubset) {
      valuesToReset.huggingface_dataset_config = subsetOptions[0].value;
    }

    const selectedSplit = currentValues.huggingface_dataset_split;
    const hasSelectedSplit = splitOptions?.some(
      ({ value }) => value === selectedSplit,
    );
    if (splitOptions?.length > 0 && !hasSelectedSplit) {
      valuesToReset.huggingface_dataset_split = splitOptions[0].value;
    }

    if (
      currentValues.num_rows === undefined ||
      currentValues.num_rows === null ||
      currentValues.num_rows === ""
    ) {
      valuesToReset.num_rows = 1;
    }

    if (Object.keys(valuesToReset).length > 0) {
      reset({ ...currentValues, ...valuesToReset });
    }
  }, [
    show,
    showNameField,
    huggingFaceDetail?.name,
    reset,
    watch,
    subsetOptions,
    splitOptions,
  ]);
  return (
    <Drawer
      open={show}
      onClose={onClose}
      anchor="right"
      slotProps={{
        backdrop: { invisible: true },
      }}
      PaperProps={{
        sx: { width: 1, maxWidth: 525 },
      }}
    >
      <IconButton
        onClick={onClose}
        sx={{ position: "absolute", top: "12px", right: "12px" }}
      >
        <Iconify icon="mingcute:close-line" />
      </IconButton>
      <HuggingDetailForm
        control={control}
        huggingFaceDetail={huggingFaceDetail}
        watch={watch}
        subsetOptions={subsetOptions}
        splitOptions={splitOptions}
        onSubmit={onSubmit}
        onClose={onClose}
        isLoadingCreateDataset={isLoadingCreateDataset}
        showNameField={showNameField}
        huggingFaceDatasetConfigError={huggingFaceDatasetConfigError}
      />
    </Drawer>
  );
};

HuggingFaceDetailDrawer.propTypes = {
  show: PropTypes.bool.isRequired,
  setShow: PropTypes.func.isRequired,
  reset: PropTypes.func.isRequired,
  control: PropTypes.object.isRequired,
  huggingFaceDetail: PropTypes.object,
  watch: PropTypes.func.isRequired,
  subsetOptions: PropTypes.array.isRequired,
  splitOptions: PropTypes.array.isRequired,
  onSubmit: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
  isLoadingCreateDataset: PropTypes.bool.isRequired,
  showNameField: PropTypes.bool.isRequired,
  huggingFaceDatasetConfigError: PropTypes.string,
};

export default HuggingFaceDetailDrawer;
