import React from "react";
import { Box, IconButton, MenuItem, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import PropTypes from "prop-types";
import useToggleAnnotationsStore from "../store";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";

const AnnotationHeaderCellRenderer = ({
  displayName,
  metricId,
  isTextType = null,
  subLabel = "",
  subLabelType = "person",
  showActions = true,
}) => {
  const popover = usePopover();
  const toggleMetric = useToggleAnnotationsStore((s) => s.toggleMetric);
  const isExpanded = useToggleAnnotationsStore((s) =>
    s.showMetricsIds.includes(metricId),
  );

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "stretch",
        justifyContent: "center",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        position: "relative",
        gap: 0.5,
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: 0.75,
          overflow: "hidden",
          flex: 1,
          minWidth: 0,
          pr: !isTextType && showActions ? 3 : 0,
        }}
      >
        <SvgColor
          sx={{ width: "18px", flexShrink: 0 }}
          src="/assets/icons/ic_label.svg"
        />
        <Typography
          typography={"s2_1"}
          fontWeight={"fontWeightMedium"}
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            minWidth: 0,
          }}
        >
          {displayName}
        </Typography>
      </Box>
      <ShowComponent condition={Boolean(subLabel)}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            minWidth: 0,
            pl: 0.25,
          }}
        >
          <SvgColor
            sx={{ width: 16, flexShrink: 0 }}
            src={
              subLabelType === "average"
                ? "/assets/icons/ic_average.svg"
                : "/assets/icons/ic_single_person.svg"
            }
          />
          <Typography
            typography="s2_1"
            sx={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              minWidth: 0,
              color: "text.secondary",
            }}
          >
            {subLabel}
          </Typography>
        </Box>
      </ShowComponent>
      <ShowComponent condition={!isTextType && showActions}>
        <IconButton
          size="small"
          onClick={popover.onOpen}
          sx={{
            position: "absolute",
            right: 2,
            top: "50%",
            transform: "translateY(-50%)",
          }}
        >
          <Iconify icon="eva:more-vertical-fill" width={16} height={16} />
        </IconButton>
      </ShowComponent>

      <CustomPopover
        open={popover.open}
        onClose={popover.onClose}
        arrow="top-right"
      >
        <MenuItem
          onClick={() => {
            toggleMetric(metricId);
            popover.onClose();
          }}
        >
          <Iconify icon={isExpanded ? "eva:eye-off-fill" : "eva:eye-fill"} />
          {isExpanded ? "Hide responses" : "View all responses"}
        </MenuItem>
      </CustomPopover>
    </Box>
  );
};

AnnotationHeaderCellRenderer.propTypes = {
  displayName: PropTypes.string,
  metricId: PropTypes.string,
  isTextType: PropTypes.bool,
  subLabel: PropTypes.string,
  subLabelType: PropTypes.oneOf(["average", "person"]),
  showActions: PropTypes.bool,
};

export default AnnotationHeaderCellRenderer;
