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

    const defaultValues = { num_rows: 1 };
    if (showNameField && huggingFaceDetail?.name) {
      defaultValues.name = huggingFaceDetail.name;
    }
    if (subsetOptions?.length > 0) {
      defaultValues.huggingface_dataset_config = subsetOptions[0].value;
    }
    if (splitOptions?.length > 0) {
      defaultValues.huggingface_dataset_split = splitOptions[0].value;
    }
    reset(defaultValues);
  }, [
    show,
    showNameField,
    huggingFaceDetail?.name,
    reset,
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
