import React, { useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Typography,
} from "@mui/material";
import { GridExpandMoreIcon } from "@mui/x-data-grid";
import PropertyFields from "./PropertyFields";
import PropTypes from "prop-types";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import SvgColor from "src/components/svg-color";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const AddColumnField = ({
  control,
  index,
  remove,
  fields,
  columns,
  addNProps,
  newProps,
}) => {
  const [showConfirmation, setShowConfirmation] = useState(false);
  const handleDelete = () => {
    remove(index);
    setShowConfirmation(false);
  };

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "border.default",
        borderRadius: "8px",
      }}
    >
      <ConfirmDialog
        open={showConfirmation}
        maxWidth="sm"
        onClose={() => setShowConfirmation(false)}
        title={`Are you sure you want to delete Column ${columns?.[index]?.name ? index + 1 + ": " + columns?.[index]?.name : ""}`}
        content={`Delete will remove it from columns`}
        action={
          <LoadingButton
            variant="contained"
            type="button"
            color="error"
            size="small"
            onClick={handleDelete}
          >
            Yes, delete
          </LoadingButton>
        }
      />
      <Accordion defaultExpanded>
        <AccordionSummary
          expandIcon={<GridExpandMoreIcon />}
          sx={{
            textAlign: "right",
            flexDirection: "row",
            color: "text.secondary",
            minHeight: "16px !important",
            "& .MuiAccordionSummary-content": {
              padding: "12px 0 12px 8px",
              margin: 0,
            },
            "& .MuiAccordionSummary-content.Mui-expanded": {
              margin: "0px",
            },
            "&.MuiAccordionSummary-root": {
              padding: "0px 12px",
            },
          }}
        >
          <Box
            sx={{
              width: "100%",
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <Typography
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
              variant="s1"
            >
              Column {index + 1}
            </Typography>
            {fields?.length > 1 && (
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{
                  height: "20px",
                  width: "20px",
                  cursor: "pointer",
                  bgcolor: "text.primary",
                  marginRight: "12px",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  setShowConfirmation(true);
                }}
              />
            )}
          </Box>
        </AccordionSummary>
        <AccordionDetails sx={{ padding: "0px 16px 16px" }}>
          <Box sx={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <Box sx={{ width: "100%", display: "flex", gap: "12px" }}>
              <FormTextFieldV2
                label="Column Name"
                placeholder="Enter name"
                size="small"
                control={control}
                fieldName={`columns.${index}.name`}
                fullWidth
              />
              <FormSearchSelectFieldControl
                fullWidth
                control={control}
                fieldName={`columns.${index}.data_type`}
                size="small"
                label="Column Type"
                options={[
                  { label: "Text", value: "text" },
                  { label: "Boolean", value: "boolean" },
                  { label: "Integer", value: "integer" },
                  { label: "Float", value: "float" },
                  { label: "Json", value: "json" },
                  { label: "Array", value: "array" },
                  // { label: "Image", value: "image" },
                  { label: "Datetime", value: "datetime" },
                ]}
              />
              <Box sx={{ width: "40px" }} />
            </Box>
            <PropertyFields
              control={control}
              nestIndex={index}
              addNProps={addNProps}
              newProps={newProps}
            />
          </Box>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
};

export default AddColumnField;

AddColumnField.propTypes = {
  control: PropTypes.any,
  index: PropTypes.number,
  remove: PropTypes.func,
  error: PropTypes.any,
  fields: PropTypes.array,
  columns: PropTypes.array,
  addNProps: PropTypes.func,
  newProps: PropTypes.array,
};
