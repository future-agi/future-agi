import * as React from "react";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import { Box, IconButton, Typography, useTheme } from "@mui/material";
import Iconify from "../iconify";
import PropTypes from "prop-types";
import SliderRow from "src/sections/common/SliderRow/SliderRow";
import { useForm } from "react-hook-form";

export default function AccordionSlider({
  sno = 1,
  sliderText = "",
  header,
  isDeleteAble,
  step = 1,
  min = 1,
  max = 100,
}) {
  const theme = useTheme();
  const [isExpanded, setIsExpanded] = React.useState(false);
  const slideCaptilize = sliderText
    .concat(" " + sno + " : ")
    .split(" ")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");

  const { control } = useForm({
    defaultValues: {
      slider1: step,
    },
  });

  return (
    <Box padding={1}>
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
              backgroundColor: "status.info.bg",
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
            borderColor: "divider",
          }}
        >
          <SliderRow
            label={slideCaptilize}
            control={control}
            fieldName={`slider1`}
            min={min}
            max={max}
            step={step || 10}
            disabled={true}
          />
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}

AccordionSlider.propTypes = {
  sno: PropTypes.number,
  sliderText: PropTypes.string,
  header: PropTypes.string,
  isDeleteAble: PropTypes.bool,
  step: PropTypes.any,
  min: PropTypes.any,
  max: PropTypes.any,
};
