import {
  Box,
  Checkbox,
  Chip,
  FormControlLabel,
  FormGroup,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useForm, useWatch } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";

const CategoricalFields = ({
  handleAddProperty,
  remove,
  fields,
  fieldName,
  update,
  isDistributionUniform,
  setIsDistributionUniform,
  handleMove,
}) => {
  const { control, reset, watch } = useForm({
    defaultValues: {
      category: "",
    },
  });

  const value = watch("category");

  const addButton = async () => {
    if (!value) return;
    await handleAddProperty(
      { type: value, value: "", category: value },
      { focusName: "category" },
    );
    reset();
    if (isDistributionUniform) {
      update();
    }
    handleMove();
  };

  const property = useWatch({
    control: fields,
    name: fieldName,
  });

  const handleUpdate = (e) => {
    const { checked } = e.target;
    setIsDistributionUniform(checked);
    if (!checked) return;
    update();
  };
  return (
    <React.Fragment>
      <Box sx={{ display: "flex", gap: "12px", alignItems: "center" }}>
        <FormTextFieldV2
          autoFocus
          label="Name"
          size="small"
          control={control}
          fieldName="category"
          placeholder="Enter category"
          fullWidth
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addButton();
            }
          }}
        />
        <Iconify
          icon="solar:add-linear"
          color="text.primary"
          sx={{
            cursor: "pointer",
          }}
          onClick={addButton}
        />
        <Box sx={{ width: "20px" }} />
      </Box>
      <Box sx={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
        {property?.map((item, index) => {
          if (item.category) {
            return (
              <Chip
                key={index}
                label={item.type}
                size="small"
                deleteIcon={
                  <Iconify
                    icon="solar:close-linear"
                    width="12px"
                    height="12px"
                  />
                }
                sx={{
                  backgroundColor: "action.hover",
                  color: "text.primary",
                  "&:hover": {
                    backgroundColor: "action.hover",
                    color: "text.primary",
                  },
                  "& .MuiChip-deleteIcon": {
                    color: "text.secondary",
                    "&:hover": {
                      color: "text.secondary",
                    },
                  },
                }}
                onDelete={() => remove(index)}
              />
            );
          }
        })}
      </Box>
      <FormGroup>
        <FormControlLabel
          control={
            <Checkbox
              checked={isDistributionUniform}
              size="small"
              onChange={handleUpdate}
            />
          }
          variant="body2"
          fontWeight="fontWeightRegular"
          sx={{ color: "text.primary" }}
          label="Apply uniform distribution values"
        />
      </FormGroup>
    </React.Fragment>
  );
};

export default CategoricalFields;

CategoricalFields.propTypes = {
  fieldName: PropTypes.string,
  handleAddProperty: PropTypes.func,
  remove: PropTypes.func,
  fields: PropTypes.array,
  update: PropTypes.func,
  isDistributionUniform: PropTypes.bool,
  setIsDistributionUniform: PropTypes.func,
  handleMove: PropTypes.func,
};
