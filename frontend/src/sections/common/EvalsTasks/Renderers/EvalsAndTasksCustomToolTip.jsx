import React, { useState, useEffect, useRef } from "react";
import { Box, Chip, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { alpha } from "@mui/material";

const EvalsAndTasksCustomTooltip = ({ value, colDef }) => {
  const [searchQuery] = useState("");
  const tooltipRef = useRef(null);

  const filters = value ? value.split(", ") : [];
  const filteredFilters = filters.filter((filter) =>
    filter.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  useEffect(() => {
    const handleMouseLeave = () => {
      const tooltipElement = tooltipRef.current.closest(".ag-tooltip");
      if (tooltipElement) {
        tooltipElement.style.visibility = "hidden";
      }
    };

    const tooltipElement = tooltipRef.current.closest(".ag-tooltip");
    if (tooltipElement) {
      tooltipElement.addEventListener("mouseleave", handleMouseLeave);
    }

    return () => {
      if (tooltipElement) {
        tooltipElement.removeEventListener("mouseleave", handleMouseLeave);
      }
    };
  }, []);

  return (
    <Box
      ref={tooltipRef}
      sx={{
        backgroundColor: "background.paper",
        border: "1px solid lightgray",
        borderRadius: "12px",
        // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
        boxShadow: "-5px 5px 10px rgba(0, 0, 0, 0.1)",
        fontSize: "13px",
        paddingBottom: "4px",
        paddingX: 1,
        color: "text.primary",
        Width: "400px",
        wordBreak: "break-word",
        maxHeight: "180px",
        overflowY: "auto",
        minWidth: "200px",
        "&::-webkit-scrollbar": {
          width: "6px", // Thin scrollbar
        },
        "&::-webkit-scrollbar-thumb": {
          backgroundColor: "lightgray", // Thumb color
          borderRadius: "4px", // Rounded scrollbar thumb
        },
        "&::-webkit-scrollbar-thumb:hover": {
          backgroundColor: "gray", // Thumb color on hover
        },
        "&::-webkit-scrollbar-track": {
          backgroundColor: "action.hover", // Track color
        },
        "&::-webkit-scrollbar-button": {
          display: "none", // Removes top and bottom arrows
        },
      }}
    >
      {/* Sticky Search Bar */}
      <Box
        sx={{
          position: "sticky",
          top: 0,
          backgroundColor: "background.paper",
          zIndex: 1,
          padding: "4px",
        }}
      >
        {/* <TextField
          variant="outlined"
          size="small"
          placeholder="Search filters"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          sx={{
            width: "100%",
            color: "text.disabled",
          }}
          InputProps={{
            disableUnderline: true,
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
                  height={25}
                  width={25}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
        /> */}
        <Typography
          variant="s2"
          sx={{
            fontWeight: "fontWeightSemiBold",
            color: "text.primary",
            marginTop: "8px",
            display: "block",
          }}
        >
          Added {colDef?.field === "evalsApplied" ? "Evals" : "Filters"}
        </Typography>
      </Box>

      {/* Display filtered filters */}
      <Box
        sx={{
          padding: "4px",
          paddingTop: "0px",
          display: "flex",
          flexDirection: "column",
          alignItems: "start",
          gap: "10px",
          mt: 1,
        }}
      >
        {filteredFilters.length > 0 ? (
          filteredFilters.map((filter, index) => (
            <Chip
              key={index}
              label={filter}
              size="small"
              sx={{
                backgroundColor: (theme) =>
                  alpha(theme.palette.primary.main, 0.1),
                color: "primary.main",
                "&:hover": {
                  backgroundColor: (theme) =>
                    alpha(theme.palette.primary.main, 0.1),
                },
              }}
            />
          ))
        ) : (
          <Box sx={{ color: "gray", fontStyle: "italic" }}>
            No filters found
          </Box>
        )}
      </Box>
    </Box>
  );
};

EvalsAndTasksCustomTooltip.propTypes = {
  value: PropTypes.string,
  colDef: PropTypes.object,
};

EvalsAndTasksCustomTooltip.defaultProps = {
  value: "",
};

export default EvalsAndTasksCustomTooltip;
