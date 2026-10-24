import React, { useState } from "react";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import { grey } from "src/theme/palette";
import {
  Box,
  FormHelperText,
  IconButton,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "../iconify";
import PropTypes from "prop-types";
import CustomTooltip from "../tooltip/CustomTooltip";

export default function EditableAccordionText({
  sno = 1,
  sliderText = "",
  header,
  isDeleteAble,
  placeholderText = "",
  min = 0,
  max = 10,
  editableText = "",
  disabled = false,
  onTextChange = () => {}, // Callback for text changes
  errors,
  isExpanded,
  setIsExpanded,
}) {
  const theme = useTheme();
  const [localText, setLocalText] = useState(editableText);

  // Handle text input change
  const handleInputChange = (event) => {
    setLocalText(event.target.value); // Update local state
  };

  // Notify parent when editing is done
  const handleBlur = () => {
    onTextChange(localText); // Pass the updated text to the parent component
  };

  return (
    <Box sx={{ padding: "16px 0px" }}>
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
            <Box sx={{ display: "flex", alignItems: "center" }}>
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
          sx={{ borderTop: "1px solid gray", borderColor: grey["300"] }}
        >
          <CustomTooltip
            show={disabled}
            title="You can't annotate a label which is already done"
          >
            <Box>
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "row",
                  gap: "5px",
                  pointerEvents: disabled ? "none" : "auto",
                  opacity: disabled ? 0.5 : 1,
                }}
              >
                <Typography
                  variant="body2"
                  sx={{ padding: "5px", textTransform: "capitalize" }}
                >
                  {sliderText}:{" "}
                </Typography>
                <Box flexGrow={1} sx={{ pt: "5px" }}>
                  <TextField
                    fullWidth
                    value={localText}
                    onChange={handleInputChange}
                    onBlur={handleBlur}
                    multiline
                    minRows={3}
                    variant="outlined"
                    color="primary"
                    size="small"
                    placeholder={placeholderText}
                    inputProps={{ minLength: min, maxLength: max }}
                    sx={{
                      fontSize: 13,
                      fontWeight: theme.typography.fontWeightRegular,
                      lineHeight: 1.6,
                      backgroundColor: "action.hover",
                    }}
                  />
                  {errors && (
                    <FormHelperText
                      error={errors}
                      sx={{ mb: "4px", fontWeight: "500" }}
                    >
                      {errors.message}
                    </FormHelperText>
                  )}
                  <Typography
                    color="text.secondary"
                    sx={{
                      fontSize: "12px",
                      fontWeight: theme.typography.fontWeightRegular,
                    }}
                  >
                    {localText?.length > min ? localText?.length : min}/{max}
                  </Typography>
                </Box>
              </Box>
            </Box>
          </CustomTooltip>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}

EditableAccordionText.propTypes = {
  sno: PropTypes.number,
  sliderText: PropTypes.string,
  header: PropTypes.string,
  isDeleteAble: PropTypes.bool,
  placeholderText: PropTypes.any,
  min: PropTypes.any,
  max: PropTypes.any,
  editableText: PropTypes.string,
  disabled: PropTypes.bool,
  onTextChange: PropTypes.func, // Callback for notifying parent about text changes
  errors: PropTypes.object,
  isExpanded: PropTypes.bool,
  setIsExpanded: PropTypes.func,
};
