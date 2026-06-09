import { Box, Chip } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { normalizeEvalCellValue } from "src/sections/develop-detail/DataTab/common";

const StatusCellRenderer = ({ value }) => {
    const normalized = normalizeEvalCellValue(value);
    let displayValue = normalized;
    if (Array.isArray(normalized)) {
        displayValue = normalized[0];
    } else if (normalized && typeof normalized === "object") {
        displayValue =
            typeof normalized.score === "number"
                ? normalized.score * 100
                : (normalized.choice ?? "");
    }

    let color;
    if (displayValue >= 0 && displayValue <= 49) {
        color = 'error';
    } else if (displayValue >= 50 && displayValue <= 79) {
        color = 'warning';
    } else if (displayValue >= 80 && displayValue <= 100) {
        color = 'success';
    }

    const isNumeric = typeof displayValue === "number" && !isNaN(displayValue);
    const label = isNumeric
        ? `${displayValue}%`
        : displayValue == null || displayValue === ""
          ? "Error"
          : String(displayValue);

    return (
        <Box>
            <Chip
                variant="soft"
                label={label}
                size="small"
                color={color}
                sx={{
                    paddingX: "4px"
                }}
            />
        </Box>
    );
};

StatusCellRenderer.propTypes = {
    value: PropTypes.any,
};


export default StatusCellRenderer;