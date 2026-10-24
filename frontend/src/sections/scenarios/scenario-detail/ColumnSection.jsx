import { Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CreateScenarioHeader from "../CreateScenarioHeader";
import SvgColor from "src/components/svg-color";
import { useFieldArray } from "react-hook-form";
import { ColumnCard } from "./ColumnCard";

const ColumnSection = ({ control, errors }) => {
  const { fields, remove, append } = useFieldArray({
    control,
    name: "columns",
  });

  const addColumn = () => {
    append({
      name: "",
      type: "",
      description: "",
    });
  };

  return (
    <Box>
      <CreateScenarioHeader
        title="Columns"
        description="Add custom inputs like values, ranges, or tags. These will be used to generate detailed scenario variations."
        rightSection={
          <Button
            variant="outlined"
            color="primary"
            sx={{ textWrap: "nowrap", marginLeft: 1 }}
            size="small"
            onClick={addColumn}
            startIcon={
              <SvgColor
                src="/assets/icons/components/ic_add.svg"
                sx={{ color: "inherit" }}
              />
            }
          >
            Add Column
          </Button>
        }
      />

      <Box sx={{ display: "flex", flexDirection: "column", gap: 2, my: 1 }}>
        {fields.map((col, index) => (
          <ColumnCard
            key={col.id}
            control={control}
            index={index}
            removeColumn={() => remove(index)}
            removable={true}
            ColumnError={errors?.columns?.[index] ?? false}
          />
        ))}
      </Box>
    </Box>
  );
};

ColumnSection.propTypes = {
  control: PropTypes.object.isRequired,
  errors: PropTypes.object,
};

export default ColumnSection;
