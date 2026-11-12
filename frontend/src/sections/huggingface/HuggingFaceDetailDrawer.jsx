import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { Drawer, IconButton } from "@mui/material";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import HuggingDetailForm from "./HuggingDetailForm";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const HuggingFaceDetailDrawer = ({
  show,
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
  config,
  setValue,
  revision,
  onRevision,
  targetDatasetId,
}) => {
  const subset = useWatch({ control, name: "huggingface_dataset_config" });
  const split = useWatch({ control, name: "huggingface_dataset_split" });
  const name = useWatch({ control, name: "name" });
  const numRowsOf = (c, sp) => config?.dataset_info?.num_rows?.[c]?.[sp];

  // W3-7: on open and on a revision change, keep the config and split
  // when the revision has them (else its first ones) and import the whole
  // split (the number can be lowered). A typed name stays.
  useEffect(() => {
    if (!show || !config) return;
    const byConfig = config.dataset_info?.num_rows ?? {};
    const nextConfig =
      subset && byConfig[subset] ? subset : Object.keys(byConfig)[0] ?? "";
    const splits = Object.keys(byConfig[nextConfig] ?? {});
    const nextSplit = split && splits.includes(split) ? split : splits[0] ?? "";
    if (showNameField && !String(name ?? "").trim())
      setValue?.("name", huggingFaceDetail?.name ?? "");
    setValue?.("huggingface_dataset_config", nextConfig);
    setValue?.("huggingface_dataset_split", nextSplit);
    setValue?.("num_rows", numRowsOf(nextConfig, nextSplit) ?? 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    show,
    showNameField,
    huggingFaceDetail?.name,
    config?.revision,
    config?.dataset_info?.revision,
  ]);

  // A config without the chosen split moves to its first split.
  useEffect(() => {
    if (!show || !config || !subset) return;
    const splits = Object.keys(config.dataset_info?.num_rows?.[subset] ?? {});
    if (split && splits.includes(split)) return;
    if (splits[0]) setValue?.("huggingface_dataset_split", splits[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subset]);

  // Choosing a split imports all of it unless the number is changed after.
  useEffect(() => {
    if (!show || !subset || !split) return;
    const n = numRowsOf(subset, split);
    if (n != null) setValue?.("num_rows", n);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subset, split]);
  const covered = useDrawerCovered(show);

  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      open={show}
      onClose={onClose}
      anchor="right"
      slotProps={{
        backdrop: { invisible: true },
      }}
      PaperProps={{
        sx: { width: 1, maxWidth: 640 },
      }}
    >
      <IconButton
        onClick={onClose}
        sx={{ position: "absolute", top: "12px", right: "12px" }}
      >
        <Iconify icon="solar:close-linear" />
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
        config={config}
        revision={revision}
        onRevision={onRevision}
        targetDatasetId={targetDatasetId}
      />
    </Drawer>
  );
};

HuggingFaceDetailDrawer.propTypes = {
  show: PropTypes.bool.isRequired,
  setShow: PropTypes.func.isRequired,
  reset: PropTypes.func,
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
  config: PropTypes.object,
  setValue: PropTypes.func,
  revision: PropTypes.string,
  onRevision: PropTypes.func,
  targetDatasetId: PropTypes.string,
};

export default HuggingFaceDetailDrawer;
