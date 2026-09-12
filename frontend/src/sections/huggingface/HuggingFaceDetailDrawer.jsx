import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { Drawer, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import HuggingDetailForm from "./HuggingDetailForm";

const HuggingFaceDetailDrawer = ({
  show,
  getValues,
  setValue,
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

    if (showNameField && huggingFaceDetail?.name && !getValues("name")) {
      setValue("name", huggingFaceDetail.name);
    }

    const currentSubset = getValues("huggingface_dataset_config");
    if (
      subsetOptions?.length > 0 &&
      !subsetOptions.some(({ value }) => value === currentSubset)
    ) {
      setValue("huggingface_dataset_config", subsetOptions[0].value);
    }

    const currentSplit = getValues("huggingface_dataset_split");
    if (
      splitOptions?.length > 0 &&
      !splitOptions.some(({ value }) => value === currentSplit)
    ) {
      setValue("huggingface_dataset_split", splitOptions[0].value);
    }

    const currentRowCount = getValues("num_rows");
    if (
      currentRowCount === undefined ||
      currentRowCount === null ||
      currentRowCount === ""
    ) {
      setValue("num_rows", 1);
    }
  }, [
    show,
    showNameField,
    huggingFaceDetail?.name,
    getValues,
    setValue,
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
  getValues: PropTypes.func.isRequired,
  setValue: PropTypes.func.isRequired,
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
