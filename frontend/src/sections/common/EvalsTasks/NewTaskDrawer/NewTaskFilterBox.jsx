import { Box, Button } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { getRandomId } from "src/utils/utils";
import NewTaskFilterRow from "./NewTaskFilterRow";
import { useFieldArray } from "react-hook-form";
import Iconify from "src/components/iconify";
import {
  FilterDefaultOperators,
  FilterDefaultValues,
} from "src/utils/constants";

const NewTaskFilterBox = ({
  control,
  attributes,
  getValues,
  setValue,
  onAttributeSearchChange,
}) => {
  const { fields, append, update } = useFieldArray({
    control,
    name: "filters",
  });

  const addFilter = () => {
    append({
      id: getRandomId(),
      propertyId: "",
      property: "",
      filterConfig: {
        filterType: "text",
        filterOp: FilterDefaultOperators["text"],
        filterValue: FilterDefaultValues["text"],
      },
    });
  };

  const removeFilter = (idx) => {
    if (Number.isNaN(idx) || idx < 0 || idx >= fields?.length) return;
    const filters = getValues(`filters`) ?? [];
    const updatedFields = filters.filter((_, index) => index !== idx);
    setValue("filters", updatedFields);
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
      }}
    >
      {fields.map((filter, index) => (
        <NewTaskFilterRow
          key={filter.id}
          index={index}
          removeFilter={removeFilter}
          control={control}
          attributes={attributes}
          update={update}
          getValues={getValues}
          onAttributeSearchChange={onAttributeSearchChange}
        />
      ))}
      <Box>
        <Button
          startIcon={
            <Iconify color="text.primary" icon="material-symbols:add" />
          }
          onClick={addFilter}
          variant="text"
          color="primary"
          size="small"
          sx={{
            fontSize: "12px",
            color: "text.disabled",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            width: "126px",
            height: "30px",
          }}
        >
          Add Filter
        </Button>
      </Box>
    </Box>
  );
};

NewTaskFilterBox.propTypes = {
  control: PropTypes.object,
  attributes: PropTypes.arrayOf(PropTypes.object),
  getValues: PropTypes.func,
  setValue: PropTypes.func,
  onAttributeSearchChange: PropTypes.func,
};

export default NewTaskFilterBox;
