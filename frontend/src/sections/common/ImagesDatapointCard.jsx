import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import Iconify from "src/components/iconify";
import GridIcon from "src/components/gridIcon/GridIcon";

const ImagesDatapointCard = ({ value, column }) => {
  const cellValue = value?.cell_value;
  const imageUrls = useMemo(() => {
    if (!cellValue) return [];
    try {
      const parsed =
        typeof cellValue === "string" ? JSON.parse(cellValue) : cellValue;
      return Array.isArray(parsed) ? parsed : [parsed];
    } catch {
      return [cellValue];
    }
  }, [cellValue]);

  const hasImages = imageUrls.length > 0;

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
          <Iconify
            icon="material-symbols:art-track-outline"
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
            {hasImages ? (
              <Box
                sx={{
                  display: "flex",
                  gap: 1,
                  flexWrap: "wrap",
                }}
              >
                {imageUrls.map((url, index) => (
                  <GridIcon
                    key={index}
                    src={url}
                    alt={`Image ${index + 1}`}
                    sx={{
                      cursor: "pointer",
                      borderRadius: "8px",
                      width: "100px",
                      height: "100px",
                      objectFit: "cover",
                    }}
                  />
                ))}
              </Box>
            ) : (
              <Box
                sx={{
                  height: 150,
                  flex: 1,
                  maxWidth: 150,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: "var(--bg-input)",
                  borderRadius: "8px",
                  border: "1px dashed var(--border-default)",
                }}
              >
                <img
                  src="/assets/placeholder.svg"
                  alt="No images placeholder"
                  style={{
                    width: "24px",
                    height: "24px",
                    opacity: 0.6,
                  }}
                />
              </Box>
            )}
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

ImagesDatapointCard.propTypes = {
  value: PropTypes.object,
  column: PropTypes.object,
};

export default ImagesDatapointCard;
