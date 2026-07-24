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

    const defaultValues = {};

    // Set name field only when creating a new dataset (showNameField=true
    // and an existing huggingFaceDetail is being edited). When adding
    // rows to an already-created dataset, showNameField=false and we
    // intentionally skip the name field — the existing dataset's name
    // should not be overwritten.
    if (showNameField && huggingFaceDetail?.name) {
      defaultValues.name = huggingFaceDetail.name;
    }

    // subset / split / num_rows must populate in BOTH flows
    // (create-new-dataset AND add-rows-to-existing-dataset). The previous
    // implementation gated `reset(defaultValues)` inside the
    // `if (showNameField && huggingFaceDetail?.name)` block, which meant
    // the add-rows flow never reset the form and the new rows were
    // submitted with stale (or missing) subset/split/num_rows values —
    // causing them to be written to the wrong location in the dataset.
    // See issue #1500.
    if (subsetOptions?.length > 0) {
      defaultValues.huggingface_dataset_config = subsetOptions[0].value;
    }
    if (splitOptions?.length > 0) {
      defaultValues.huggingface_dataset_split = splitOptions[0].value;
    }

    // Always set default row count.
    defaultValues.num_rows = 1;

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
