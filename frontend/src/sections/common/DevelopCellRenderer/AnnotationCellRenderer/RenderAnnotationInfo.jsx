import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip";

const RenderAnnotationInfo = ({ annotationData }) => {
  const annotationInfo = annotationData?.feedback_info?.annotation;
  const hasUserId = !!annotationInfo?.user_id;
  const isVerified = annotationInfo?.verified === true;
  const isAutoAnnotated = annotationInfo?.auto_annotate === true;

  return (
    <Box
      sx={{
        display: "flex",
        gap: "6px",
        lineHeight: "1.5",
        alignItems: "center",
        justifyContent: "flex-end",
      }}
    >
      {/* Changed by human */}
      <ShowComponent condition={hasUserId}>
        <CustomTooltip show={true} title={"Changed by human"} arrow>
          <Iconify
            icon="la:user-edit"
            width="24px"
            height="24px"
            sx={{ color: "cyan.main" }}
          />
        </CustomTooltip>
      </ShowComponent>

      {/* Auto annotated and verified */}
      <ShowComponent condition={isVerified}>
        <CustomTooltip show={true} title={"Auto annotated and verified"} arrow>
          <Iconify
            icon="bitcoin-icons:verify-outline"
            width="28px"
            height="28px"
            sx={{ color: "success.main" }}
          />
        </CustomTooltip>
      </ShowComponent>

      {/* Auto annotated */}
      <ShowComponent condition={isAutoAnnotated}>
        <CustomTooltip show={true} title={"Auto annotated"} arrow>
          <Iconify
            icon="solar:stars-minimalistic-linear"
            width="24px"
            height="24px"
            sx={{ color: "status.warning.fg" }}
          />
        </CustomTooltip>
      </ShowComponent>
    </Box>
  );
};

RenderAnnotationInfo.propTypes = {
  annotationMeta: PropTypes.string,
  annotationData: PropTypes.object,
};

export default RenderAnnotationInfo;
