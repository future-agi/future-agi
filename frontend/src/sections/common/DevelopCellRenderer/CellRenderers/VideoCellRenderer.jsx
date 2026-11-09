import React, { useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fileNameOf } from "src/sections/common/Values/sniff";
import { formatDuration } from "src/sections/common/Values/MediaViewers";
import RenderMeta from "../RenderMeta";
import { commonPropTypes, tooltipSlotProp } from "./cellRendererHelper";

/**
 * `video` cells (R2-SPEC P6): a poster frame (the video's first frame,
 * metadata only) with the duration, and the file name. Open the row to
 * play it.
 *
 * Props (frozen): commonPropTypes + `isHover`, `cellData`.
 */
const VideoCellRenderer = ({
  isHover,
  value,
  valueReason,
  formattedValueReason,
  originType,
  metadata,
  valueInfos,
}) => {
  const [duration, setDuration] = useState(null);
  const [error, setError] = useState(false);
  const url = typeof value === "string" ? value.trim() : "";
  return (
    <CustomTooltip
      show={Boolean(valueReason?.length)}
      title={formattedValueReason?.()}
      enterDelay={500}
      arrow
      slotProps={tooltipSlotProp}
    >
      <Box sx={{ px: 1, py: 0.5, height: "100%", minWidth: 0 }}>
        {url && (
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ height: "100%", minWidth: 0 }}
          >
            <Box
              sx={{
                position: "relative",
                height: "100%",
                maxHeight: 40,
                aspectRatio: "16 / 9",
                flexShrink: 0,
                borderRadius: 0.5,
                overflow: "hidden",
                bgcolor: "common.black",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              {!error && (
                <Box
                  component="video"
                  src={`${url}#t=0.5`}
                  preload="metadata"
                  muted
                  playsInline
                  onLoadedMetadata={(e) =>
                    setDuration(e.currentTarget.duration)
                  }
                  onError={() => setError(true)}
                  sx={{
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    display: "block",
                  }}
                />
              )}
              <Iconify
                icon={error ? "mdi:filmstrip-off" : "mdi:play"}
                width={14}
                sx={{
                  position: "absolute",
                  left: "50%",
                  top: "50%",
                  transform: "translate(-50%, -50%)",
                  color: "common.white",
                  opacity: 0.9,
                }}
              />
              {duration ? (
                <Box
                  component="span"
                  sx={{
                    position: "absolute",
                    right: 2,
                    bottom: 1,
                    px: 0.375,
                    borderRadius: 0.25,
                    fontSize: 10,
                    lineHeight: "13px",
                    color: "common.white",
                    bgcolor: "rgba(0,0,0,0.6)",
                  }}
                >
                  {formatDuration(duration)}
                </Box>
              ) : null}
            </Box>
            <Typography
              typography="s2"
              color="text.primary"
              noWrap
              sx={{ minWidth: 0 }}
            >
              {error ? "Couldn't load this video" : fileNameOf(url)}
            </Typography>
          </Stack>
        )}
        {isHover && (
          <RenderMeta
            originType={originType}
            meta={metadata}
            valuesInfo={valueInfos}
          />
        )}
      </Box>
    </CustomTooltip>
  );
};

VideoCellRenderer.propTypes = {
  ...commonPropTypes,
  isHover: PropTypes.bool,
  cellData: PropTypes.object,
};

export default React.memo(VideoCellRenderer);
