import React, { useState } from "react";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import { Box, ButtonBase, TextField, useTheme } from "@mui/material";
import Iconify from "../iconify";
import { grey } from "src/theme/palette";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";

export default function EditableAccordion({
  labelText = "",
  detailsText = "",
  onDetailsChange = () => {},
}) {
  const theme = useTheme();
  const [isExpanded, setIsExpanded] = useState(true);
  const [editableText, setEditableText] = useState(detailsText);

  const handleCopy = () => {
    navigator.clipboard.writeText(editableText);
    enqueueSnackbar("Content Copied", {
      variant: "success",
    });
  };

  const handleInputChange = (e) => {
    setEditableText(e.target.value);
  };

  const handleBlur = () => {
    onDetailsChange(editableText);
  };

  return (
    <Box>
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
                size="medium"
                sx={{
                  transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
                  transition: "transform 0.2s ease",
                }}
              >
                <Iconify icon="solar:alt-arrow-down-linear" />
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
            {/* {!isExpanded ? detailsText : labelText} */}
            {!isExpanded ? editableText : labelText}
          </Typography>
        </AccordionSummary>

        <AccordionDetails
          sx={{
            backgroundColor: "action.hover",
            // maxHeight: "400px",
            overflowY: "auto",
            border: "1px solid gray",
            borderColor: grey["300"],
            maxHeight: "707px",
            padding: "0px !important",
          }}
        >
          <TextField
            fullWidth
            value={editableText}
            onChange={handleInputChange}
            onBlur={handleBlur}
            multiline
            rows={15}
            variant="outlined"
            color="primary"
            size="small"
            sx={{
              fontSize: 13,
              fontWeight: theme.typography.fontWeightRegular,
              lineHeight: 1.6,
              height: "100%",
              overflowY: "auto",
              border: "none !important",
            }}
          />
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}

EditableAccordion.propTypes = {
  labelText: PropTypes.string,
  detailsText: PropTypes.string,
  onDetailsChange: PropTypes.func,
};
