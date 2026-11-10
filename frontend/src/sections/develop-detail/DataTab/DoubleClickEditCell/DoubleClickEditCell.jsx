import React, { useMemo } from "react";
import {
  Box,
  Dialog,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import EditImageZoom from "./EditImageZoom";
import EditImages from "./EditImages";
import AudioWaveformModal from "src/components/custom-audio/AudioPlayerModal";
import { useEditCellStore } from "../../states";
import { onCellValueChangedWrapper } from "../common";
import { useQueryClient } from "@tanstack/react-query";
import EditFile from "./EditFile";
import EditPersona from "./EditPersona";
import PropTypes from "prop-types";

const DoubleClickEditCell = ({ dataset }) => {
  const { editCell: params, setEditCell } = useEditCellStore();
  const onClose = () => {
    setEditCell(null);
  };
  const open = Boolean(params);
  const [, setImageZoomModal] = React.useState(false);
  const dataType = params?.colDef?.dataType;
  const queryClient = useQueryClient();

  const onCellValueChanged = onCellValueChangedWrapper(queryClient, dataset);

  const formattedValue = useMemo(() => {
    if (dataType !== "persona") return {};
    if (!params?.value) return {};
    try {
      if (typeof params?.value === "string") {
        return JSON.parse(params?.value);
      }
      return params?.value;
    } catch (_) {
      return {};
    }
  }, [params?.value, dataType]);

  const TitleComponent = () => {
    const capitalized = dataType?.charAt(0).toUpperCase() + dataType?.slice(1);
    return dataType === "audio" ? (
      <Box
        display="flex"
        justifyContent="space-between"
        alignItems="center"
        p={2}
        pb={0}
      >
        <Box>
          <Typography color="text.primary" fontSize="16px" fontWeight="600">
            Edit {capitalized}
          </Typography>
        </Box>
        <IconButton
          aria-label="close-image"
          onClick={onClose}
          sx={{
            color: "text.primary",
            p: 0,
          }}
        >
          <Iconify width="24px" icon="solar:close-linear" />
        </IconButton>
      </Box>
    ) : (
      <DialogTitle
        sx={{
          gap: "5px",
          display: "flex",
          flexDirection: "column",
          padding: 2,
          paddingY: 1,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography fontWeight={600} color={"text.primary"} fontSize="16px">
            Edit {capitalized}
          </Typography>
          <IconButton
            aria-label="close-image"
            onClick={onClose}
            sx={{ color: "text.primary" }}
          >
            <Iconify width="24px" icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>
    );
  };

  return (
    <>
      <Dialog
        open={open && dataType !== "persona"}
        onClose={onClose}
        maxWidth={false}
        PaperProps={{
          sx: {
            maxWidth: 960,
            borderRadius: "0px !important",
            minWidth: "250px",
          },
        }}
      >
        {open && dataType !== null && <TitleComponent />}
        {open && dataType === "audio" ? (
          <AudioWaveformModal
            params={params}
            onClose={onClose}
            onCellValueChanged={onCellValueChanged}
          />
        ) : open && dataType === "image" ? (
          <EditImageZoom
            params={params}
            onClose={onClose}
            onCellValueChanged={onCellValueChanged}
            setImageZoomModal={setImageZoomModal}
          />
        ) : open && dataType === "images" ? (
          <EditImages
            params={params}
            onClose={onClose}
            onCellValueChanged={onCellValueChanged}
          />
        ) : open && dataType === "document" ? (
          <EditFile
            params={params}
            onClose={onClose}
            onCellValueChanged={onCellValueChanged}
          />
        ) : null}
      </Dialog>
      <EditPersona
        editPersona={formattedValue}
        open={open && dataType === "persona"}
        onClose={onClose}
        params={params}
        onCellValueChanged={onCellValueChanged}
      />
    </>
  );
};

DoubleClickEditCell.propTypes = {
  dataset: PropTypes.string,
};
export default DoubleClickEditCell;

DoubleClickEditCell.propTypes = {};
