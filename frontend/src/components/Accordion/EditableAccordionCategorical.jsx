import * as React from "react";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import { grey } from "src/theme/palette";
import {
  Box,
  IconButton,
  TextField,
  Typography,
  useTheme,
  alpha,
} from "@mui/material";
import Iconify from "../iconify";
import PropTypes from "prop-types";
import CustomTooltip from "../tooltip/CustomTooltip";

export default function EditableAccordionCategorical({
  sno = 1,
  sliderText = "",
  header,
  isDeleteAble,
  options = [],
  autoAnnotate = false,
  onOptionSelect = () => {}, // Callback for selected option
  disabled = false,
  onDescriptionChange,
  labelValue,
  description,
  isExpanded,
  setIsExpanded,
}) {
  const theme = useTheme();

  const handleOptionClick = (selectedOption) => {
    onOptionSelect(selectedOption); // Pass the selected option to the parent component
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
            <CustomTooltip show title="Auto annotated" placement="bottom" arrow>
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
            </CustomTooltip>
          )}
          <Typography
            variant="body2"
            sx={{ marginLeft: autoAnnotate ? "70px" : "40px" }}
          >
            {header}
          </Typography>
        </AccordionSummary>
        <AccordionDetails
          sx={{
            borderTop: "1px solid gray",
            borderColor: grey["300"],
          }}
        >
          <CustomTooltip
            show={disabled}
            title="You can't annotate a label which is already done"
          >
            <Box>
              <Box
                sx={{
                  opacity: disabled ? 0.5 : 1,
                  pointerEvents: disabled ? "none" : "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: "20px",
                }}
              >
                <Box sx={{ display: "flex", flexDirection: "row", gap: "5px" }}>
                  <Typography
                    variant="body2"
                    sx={{
                      padding: "5px",
                      flex: 3,
                      textTransform: "capitalize",
                    }}
                  >
                    {sliderText}:{" "}
                  </Typography>
                  <Box
                    display={"flex"}
                    alignItems={"center"}
                    sx={{
                      flex: 7,
                      flexWrap: "wrap",
                    }}
                  >
                    {options.map((item, index, array) => {
                      const isSelected = labelValue?.includes(item?.label);

                      return (
                        <Typography
                          key={index}
                          color="black"
                          bgcolor={"background.neutral"}
                          fontWeight={"600"}
                          sx={{
                            fontSize: "13px",
                            padding: "5px 10px",
                            borderLeft:
                              index === 0 ? "none" : "1px solid black",
                            borderRight:
                              index === array.length - 1
                                ? "none"
                                : "1px solid black",
                            borderColor: grey["300"],
                            borderRadius:
                              index === 0
                                ? "8px 0 0 8px"
                                : index === array.length - 1
                                  ? "0 8px 8px 0"
                                  : "0",
                            cursor: "pointer",
                            transition: "background-color 0.2s ease",
                            backgroundColor: isSelected
                              ? grey["400"]
                              : "background.neutral", // Highlight selected
                            "&:hover": {
                              backgroundColor: grey["300"],
                            },
                          }}
                          onClick={() => handleOptionClick(item)} // Handle click
                        >
                          {item.label}
                        </Typography>
                      );
                    })}
                  </Box>
                </Box>
                <TextField
                  fullWidth
                  multiline
                  placeholder="Enter description here (optional)"
                  label="Description"
                  rows={3}
                  sx={{
                    backgroundColor: "background.neutral",
                    borderRadius: "8px",
                  }}
                  value={description}
                  onChange={(e) => onDescriptionChange(e.target.value)}
                />
              </Box>
            </Box>
          </CustomTooltip>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}

EditableAccordionCategorical.propTypes = {
  sno: PropTypes.number,
  sliderText: PropTypes.string,
  header: PropTypes.string,
  isDeleteAble: PropTypes.bool,
  options: PropTypes.array, // Array of options
  autoAnnotate: PropTypes.bool,
  disabled: PropTypes.bool,
  onOptionSelect: PropTypes.func, // Callback for option selection
  onDescriptionChange: PropTypes.func,
  labelValue: PropTypes.array,
  description: PropTypes.string,
  isExpanded: PropTypes.bool,
  setIsExpanded: PropTypes.func,
};
