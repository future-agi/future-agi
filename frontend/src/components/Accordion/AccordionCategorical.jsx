import * as React from "react";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import { grey } from "src/theme/palette";
import { Box, IconButton, Typography, useTheme, alpha } from "@mui/material";
import Iconify from "../iconify";
import PropTypes from "prop-types";

export default function AccordionCategorical({
  sno = 1,
  sliderText = "",
  header,
  isDeleteAble,
  options = [],
  autoAnnotate = false,
}) {
  const theme = useTheme();
  const [isExpanded, setIsExpanded] = React.useState(false);

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
              backgroundColor: alpha(theme.palette.success.main, 0.2),
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

          {autoAnnotate && (
            <IconButton
              size="small"
              sx={{
                position: "absolute",
                top: "7px",
                left: "50px",
              }}
            >
              <Iconify
                color="text.primary"
                icon="flat-color-icons:flash-auto"
                height={24}
                width={24}
              />
            </IconButton>
          )}
          <Typography
            variant="body2"
            sx={{
              marginLeft: autoAnnotate ? "70px" : "40px",
              textTransform: "capitalize",
            }}
          >
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
          <Box display={"flex"} alignItems={"center"}>
            {options.map((item, index, array) => (
              <Typography
                key={index}
                color="black"
                bgcolor={"background.neutral"}
                fontWeight={"600"}
                sx={{
                  fontSize: "13px",
                  padding: "5px 10px",
                  borderLeft: index === 0 ? "none" : "1px solid black",
                  borderRight:
                    index === array.length - 1 ? "none" : "1px solid black",
                  borderColor: grey["300"],
                  borderRadius:
                    index === 0
                      ? "8px 0 0 8px"
                      : index === array.length - 1
                        ? "0 8px 8px 0"
                        : "0",
                }}
              >
                {item.label}
              </Typography>
            ))}
          </Box>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}

AccordionCategorical.propTypes = {
  sno: PropTypes.number,
  sliderText: PropTypes.string,
  header: PropTypes.string,
  isDeleteAble: PropTypes.bool,
  options: PropTypes.array,
  autoAnnotate: PropTypes.bool,
};
