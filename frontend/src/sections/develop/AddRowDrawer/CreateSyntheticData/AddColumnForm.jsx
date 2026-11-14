import { LoadingButton } from "@mui/lab";
import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useFieldArray, useWatch } from "react-hook-form";
import AddColumnField from "./AddColumnField";

const AddColumnForm = ({
  control,
  handleNextTab,
  setNewProps,
  newProps,
  reset,
}) => {
  const { fields, append, remove } = useFieldArray({
    name: "columns",
    control,
  });

  const columns = useWatch({
    control,
    name: "columns",
  });

  const handleAddColumn = () => {
    append({
      name: "",
      data_type: "",
      property: [],
      description: "",
    });
    setNewProps((pre) => [...pre, []]);
  };

  const handleRemove = (ind) => {
    remove(ind);
    const currentValues = control._formValues;
    reset(currentValues); // Reset form with updated values
    setNewProps((pre) => {
      return pre.filter((_, i) => i !== ind);
    });
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        height: "calc(100% - 70px)",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "24px",
          overflowY: "auto",
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <Typography
            color="text.primary"
            variant="s1"
            fontWeight={"fontWeightSemiBold"}
          >
            Add Columns
          </Typography>
          <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
            <Typography
              variant="s2"
              color="text.secondary"
              fontWeight={"fontWeightRegular"}
            >
              Define column types and properties
            </Typography>
          </Box>
        </Box>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          {fields?.map((item, index) => {
            const addNProps = (item) => {
              setNewProps((pre) => {
                const newProperty = [...pre];
                newProperty[index] = [...newProperty[index], item];
                return newProperty;
              });
            };

            return (
              <AddColumnField
                key={item.id}
                control={control}
                index={index}
                remove={handleRemove}
                fields={fields}
                columns={columns}
                addNProps={addNProps}
                newProps={newProps[index]}
              />
            );
          })}
          <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <LoadingButton
              // size="small"
              variant="outlined"
              color="primary"
              type="button"
              sx={{
                width: "max-content",
              }}
              onClick={handleAddColumn}
            >
              <Typography variant="s1" fontWeight={"fontWeightSemiBold"}>
                Add columns
              </Typography>
            </LoadingButton>
          </Box>
        </Box>
      </Box>
      <Box
        sx={{
          padding: "8px 0px 0px",
          textAlign: "right",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          type="button"
          sx={{
            width: "200px",
            color: "text.secondary",
          }}
          onClick={() => handleNextTab(0)}
        >
          Back
        </Button>

        <LoadingButton
          size="small"
          variant="contained"
          color="primary"
          type="button"
          sx={{ width: "200px" }}
          onClick={() => handleNextTab(2)}
          disabled={columns?.some(
            (item) =>
              !item.name ||
              !item.data_type ||
              item?.property?.some(
                (temp) => !temp.type || !(temp.value && temp.value != 0),
              ),
          )}
        >
          Next
        </LoadingButton>
      </Box>
    </Box>
  );
};

export default AddColumnForm;

AddColumnForm.propTypes = {
  control: PropTypes.any,
  handleNextTab: PropTypes.func,
  setNewProps: PropTypes.func,
  newProps: PropTypes.array,
  reset: PropTypes.func,
};
