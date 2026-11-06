import * as React from "react";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import { grey } from "src/theme/palette";
import { Box, IconButton, Typography, useTheme } from "@mui/material";
import Iconify from "../iconify";
import PropTypes from "prop-types";

export default function PreviewAccordionText({
  sno = 1,
  sliderText = "",
  header,
  isDeleteAble,
  placeholderText = "",
  min = 0,
  max = 0,
  isExpanded,
  setIsExpanded,
}) {
  const theme = useTheme();

  return (
    <Box>
      <Accordion
        sx={{
          border: `1px solid ${theme.palette.divider}`,
          overflow: "hidden",
        }}
        expanded={isExpanded}
        onChange={(_, expanded) => setIsExpanded(expanded)}
      >
        <AccordionSummary
          expandIcon={
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
              }}
            >
              <IconButton
                size="large"
                sx={{
                  transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
                  transition: "transform 0.2s ease",
                }}
              >
                <Iconify icon="solar:alt-arrow-down-linear" />
              </IconButton>

              <Box
                display={"flex"}
                alignItems={"center"}
                bgcolor={"background.default"}
                borderRadius="4px"
                sx={{ marginRight: "5px" }}
              >
                <IconButton size="small">
                  <Iconify
                    color="text.primary"
                    icon="solar:command-linear"
                    height={12}
                    width={12}
                  />
                </IconButton>
                <Typography
                  fontSize={12}
                  fontWeight={theme.typography.fontWeightMedium}
                  sx={{ paddingRight: "5px" }}
                  color="text.primary"
                >
                  {sno}
                </Typography>
              </Box>

              {isDeleteAble && (
                <IconButton
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                  }}
                >
                  <Iconify
                    icon="solar:trash-bin-trash-bold"
                    height={16}
                    width={16}
                  />
                </IconButton>
              )}
            </Box>
          }
          aria-controls="panel1-content"
          id="panel1-header"
          sx={{
            "& .MuiAccordionSummary-expandIconWrapper": {
              transform: "none !important",
            },
            position: "relative",
            "& .Mui-expanded": {
              m: 0.5,
            },
            minHeight: isExpanded ? "20px !important" : 30,
          }}
        >
          <Box
            sx={{
              backgroundColor: "primary.lighter",
              width: "40px",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "0 8px",
              marginRight: 2,
              position: "absolute",
              top: "0px",
              left: "0px",
            }}
          >
            <Typography
              sx={{
                fontSize: "13px",
                fontWeight: 600,
                color: theme.palette.text.secondary,
              }}
            >
              {sno}
            </Typography>
          </Box>

          <Typography variant="body2" sx={{ paddingLeft: "40px" }}>
            {header}
          </Typography>
        </AccordionSummary>
        <AccordionDetails
          sx={{
            borderTop: "1px solid gray",
            borderColor: grey["300"],
            display: "flex",
            flexDirection: "row",
            gap: "5px",
          }}
        >
          <Typography
            variant="body2"
            sx={{ padding: "5px", textTransform: "capitalize" }}
          >
            {sliderText}:{" "}
          </Typography>
          <Box flexGrow={1}>
            <Box
              sx={{
                width: "100%",
                minHeight: "89px",
                backgroundColor: "action.hover",
                borderRadius: "8px",
                p: "8px",
                mb: "6px",
              }}
            >
              <Typography
                color="text.primary"
                sx={{
                  fontSize: "13px",
                  fontWeight: theme.typography.fontWeightRegular,
                }}
              >
                {placeholderText.slice(0, max)}
              </Typography>
            </Box>
            <Typography
              color="text.secondary"
              sx={{
                fontSize: "12px",
                fontWeight: theme.typography.fontWeightRegular,
              }}
            >
              {min}/{max}
            </Typography>
          </Box>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}

PreviewAccordionText.propTypes = {
  sno: PropTypes.number,
  sliderText: PropTypes.string,
  header: PropTypes.string,
  isDeleteAble: PropTypes.bool,
  placeholderText: PropTypes.any,
  min: PropTypes.any,
  max: PropTypes.any,
  isExpanded: PropTypes.bool,
  setIsExpanded: PropTypes.func,
};
