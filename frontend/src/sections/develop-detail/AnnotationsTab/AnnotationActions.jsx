import { Box, IconButton } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { GENERATE_LINK } from "src/config-global";
import ConfigureAnnotations from "../Annotations/ConfigureAnnotations";
import PreviewCreatedModal from "../Annotations/PreviewCreatedModal";
import logger from "src/utils/logger";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { Events, trackEvent } from "src/utils/Mixpanel";

const AnnotationActions = ({ ...columnDataRest }) => {
  const { role } = useAuthContext();
  const { annotationId, handleSetRenderAnnotationTable } =
    columnDataRest?.value || {};
  const { dataset } = useParams();
  const [isPreviewModal, setIsPreviewModal] = useState(false);
  const [isAnnotationConfig, setIsAnnotationConfig] = useState(false);
  const iconStyle = {
    width: "30px",
    height: "26px",
    borderRadius: "8px",
    border: "1px solid var(--border-default)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };

  const handleCopyToClipboard = () => {
    const url = `${GENERATE_LINK}/dashboard/develop/${dataset}/preview/${annotationId}`;

    navigator.clipboard
      .writeText(url)
      .then(() => {
        enqueueSnackbar("Link Copied", {
          variant: "success",
        });
      })
      .catch((err) => {
        enqueueSnackbar("Failed to copy link", {
          variant: "error",
        });
        logger.error("Failed to copy the link: ", err);
      });
  };
  if (!columnDataRest?.value) return "";

  return (
    <Box display="flex" gap={2} justifyContent="center" alignItems="center">
      <Box sx={iconStyle}>
        <IconButton
          size="small"
          onClick={() => {
            setIsPreviewModal(true);
            trackEvent(Events.annPreviewViewed);
          }}
        >
          <Iconify
            icon="solar:eye-linear"
            color="text.secondary"
            sx={{ width: "20px", height: "16px" }}
          />
        </IconButton>
      </Box>

      <Box sx={iconStyle}>
        <IconButton
          size="small"
          onClick={() => {
            setIsAnnotationConfig(true);
          }}
          disabled={!RolePermission.DATASETS[PERMISSIONS.UPDATE][role]}
        >
          <Iconify
            icon="solar:settings-linear"
            color="text.secondary"
            sx={{ width: "20px", height: "16px" }}
          />
        </IconButton>
      </Box>
      <Box sx={iconStyle}>
        <IconButton
          size="small"
          onClick={() => {
            handleCopyToClipboard();
          }}
        >
          <Iconify
            icon="solar:link-linear"
            color="text.secondary"
            sx={{ width: "20px", height: "16px" }}
          />
        </IconButton>
      </Box>
      {isPreviewModal && (
        <PreviewCreatedModal
          open={isPreviewModal}
          onClose={() => setIsPreviewModal(false)}
          annotationId={annotationId}
          columnConfig={columnDataRest?.data}
        />
      )}
      {isAnnotationConfig && (
        <ConfigureAnnotations
          open={isAnnotationConfig}
          onClose={() => setIsAnnotationConfig(false)}
          annotationId={annotationId || ""}
          handleSetRenderAnnotationTable={handleSetRenderAnnotationTable}
        />
      )}
    </Box>
  );
};

AnnotationActions.propTypes = {
  columnDataRest: PropTypes.object,
};

export default AnnotationActions;
