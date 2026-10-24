import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import Iconify from "src/components/iconify";
import { useEvaluationContext } from "src/sections/common/EvaluationDrawer/context/EvaluationContext";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";

const OptimizeColumn = ({ control, allColumns, isEvaluationOpen }) => {
  const { setSelectedColumn } = useEvaluationContext();
  const theme = useTheme();

  const options = useMemo(() => {
    if (!allColumns) return [];
    return allColumns.reduce((acc, column) => {
      if (column.originType === "run_prompt") {
        acc.push({
          value: column.field,
          label: column.headerName,
        });
      }
      return acc;
    }, []);
  }, [allColumns]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {options.length > 0 ? (
        <>
          <Typography typography={"s2"} color={"text.secondary"}>
            {"Choose a column or add a prompt you want to optimize"}
          </Typography>

          <FormSearchSelectFieldControl
            fullWidth
            control={control}
            fieldName="columnId"
            size="small"
            label="Column"
            required
            options={options}
            disabled={isEvaluationOpen}
            onChange={(e) => {
              setSelectedColumn(e.target.value);
            }}
          />
        </>
      ) : (
        <Box
          sx={{
            padding: "10px",
            backgroundColor: theme.palette.background.paper,
            borderRadius: "8px",
            border: `2px solid ${theme.palette.divider}`,
            display: "flex",
            alignItems: "center",
            gap: 1,
          }}
        >
          <Iconify
            icon="solar:info-circle-bold"
            color="text.secondary"
            width={14}
          />
          <Typography variant="body2" color="text.secondary">
            Use Run Prompt before using Optimization
          </Typography>
        </Box>
      )}
    </Box>
  );
};

OptimizeColumn.propTypes = {
  control: PropTypes.object.isRequired,
  allColumns: PropTypes.array,
  isEvaluationOpen: PropTypes.bool.isRequired,
  refreshGrid: PropTypes.func,
};

export default OptimizeColumn;
