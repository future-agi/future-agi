import React, { useEffect, useState } from "react";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import { Box, ButtonBase, useTheme } from "@mui/material";
import Iconify from "../iconify";
import { grey } from "src/theme/palette";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";

export default function CustomAccordion({
  labelText = "",
  detailsText = "",
  defaultExpanded = false,
  editable = false,
}) {
  const theme = useTheme();
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  useEffect(() => {
    setIsExpanded(defaultExpanded);
  }, [defaultExpanded]);

  const handleCopy = () => {
    navigator.clipboard.writeText(detailsText);
    enqueueSnackbar("Content Copied", {
      variant: "success",
    });
  };
  return (
    <Box>
      {/* Label outside of the accordion */}
      {!isExpanded && (
        <ButtonBase
          onClick={() => setIsExpanded(true)}
          sx={{ display: "flex", mb: 1 }}
        >
          <Typography
            sx={{ color: theme.palette.text.disabled }}
            fontSize={12}
            fontWeight={600}
          >
            {labelText}
          </Typography>
        </ButtonBase>
      )}

      <Accordion
        expanded={isExpanded}
        onChange={(_, expanded) => setIsExpanded(expanded)}
        sx={{
          border: `1px solid grey`,
          borderColor: grey["300"],
          overflow: "hidden",
          boxShadow: "none",
        }}
      >
        <AccordionSummary
          expandIcon={
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  handleCopy();
                }}
              >
                <Iconify icon="solar:copy-linear" />
              </IconButton>

              <IconButton
                sx={{
                  transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
                  transition: "transform 0.2s ease",
                }}
                size="medium"
              >
                <Iconify icon={`solar:alt-arrow-down-linear`} />
              </IconButton>
            </Box>
          }
          aria-controls="panel1-content"
          id="panel1-header"
          sx={{
            "& .MuiAccordionSummary-expandIconWrapper": {
              transform: "none !important",
            },
            "& .Mui-expanded": {
              m: 0.5,
            },
            minHeight: isExpanded ? "20px !important" : 30,
          }}
        >
          <Typography
            sx={{
              color: !isExpanded
                ? theme.palette.text.primary
                : theme.palette.text.disabled,
              whiteSpace: "normal",
              display: "-webkit-box",
              WebkitBoxOrient: "vertical",
              WebkitLineClamp: 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            fontSize={13}
            fontWeight={400}
          >
            {!isExpanded ? detailsText : labelText}
          </Typography>
        </AccordionSummary>

        <AccordionDetails
          sx={{
            backgroundColor: editable ? "divider" : "background.paper",
            maxHeight: "400px",
            overflowY: "auto",
            borderTop: "1px solid gray",
            borderColor: grey["300"],
          }}
        >
          <Typography
            color={theme.palette.text.primary}
            fontSize={13}
            fontWeight={theme.typography.fontWeightRegular}
            lineHeight={1.6}
          >
            {detailsText}
          </Typography>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}

CustomAccordion.propTypes = {
  labelText: PropTypes.string,
  detailsText: PropTypes.string,
  defaultExpanded: PropTypes.bool,
  editable: PropTypes.bool,
};
