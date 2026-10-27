import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import Image from "src/components/image";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import RunningSkeletonRenderer from "./DevelopCellRenderer/CellRenderers/RunningSkeletonRenderer";

const ImageDatapointCard = ({ value, column }) => {
  const cellValue = value?.cell_value;
  const hasImage = Boolean(cellValue);
  return (
    <Accordion defaultExpanded disableGutters>
      <AccordionSummary
        aria-label={`Expand ${column?.headerName}`}
        sx={{
          flexDirection: "row",
          paddingLeft: 1,
          paddingRight: 2,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <SvgColor
            src={`/assets/icons/action_buttons/ic_image.svg`}
            sx={{ width: 20, height: 20, color: "text.secondary" }}
          />
          <Typography
            variant="s1"
            color="text.disabled"
            fontWeight="fontWeightMedium"
          >
            {column?.headerName}
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 2, pb: 1 }}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            mb: 1,
          }}
        >
          <Box
            sx={{
              px: 2,
              py: 1.5,
              backgroundColor: "background.default",
              borderRadius: "6px",
            }}
          >
            <ShowComponent condition={value?.status === "running"}>
              <RunningSkeletonRenderer />
            </ShowComponent>
            <ShowComponent
              condition={value?.status === "pass" || value?.status === "error"}
            >
              {hasImage ? (
                <Image
                  height="100%"
                  src={cellValue}
                  alt=""
                  style={{
                    cursor: "pointer",
                    borderRadius: "8px",
                    maxWidth: "150px",
                  }}
                />
              ) : (
                <Box
                  sx={{
                    height: 150,
                    flex: 1,
                    maxWidth: 150,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    backgroundColor: "background.neutral",
                    borderRadius: "8px",
                    border: "1px dashed",
                    borderColor: "divider",
                  }}
                >
                  <img
                    src="/assets/placeholder.svg"
                    alt="No image placeholder"
                    style={{
                      width: "24px",
                      height: "24px",
                      opacity: 0.6,
                    }}
                  />
                </Box>
              )}
            </ShowComponent>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

ImageDatapointCard.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
};

export default ImageDatapointCard;
