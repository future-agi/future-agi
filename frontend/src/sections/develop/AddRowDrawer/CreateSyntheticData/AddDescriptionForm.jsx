import { LoadingButton } from "@mui/lab";
import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CreateDescriptions from "../CreateDescriptions";
import { useFieldArray, useWatch } from "react-hook-form";

const AddDescriptionForm = ({
  control,
  handleNextTab,
  isPending,
  editMode,
}) => {
  const { fields } = useFieldArray({
    name: "columns",
    control,
  });

  const columns = useWatch({
    control,
    name: "columns",
  });

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
            typography="s1"
            fontWeight={"fontWeightSemiBold"}
          >
            Add description
          </Typography>
          <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
            <Typography
              typography="s2"
              color="text.secondary"
              fontWeight={"fontWeightRegular"}
            >
              Define Description of the the columns.
            </Typography>
          </Box>
        </Box>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <CreateDescriptions fields={fields} control={control} />
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
          onClick={() => handleNextTab(1)}
        >
          Back
        </Button>

        <LoadingButton
          size="small"
          variant="contained"
          color="primary"
          type="submit"
          sx={{ width: "200px" }}
          disabled={columns.some((item) => !item.description)}
          loading={isPending}
        >
          {editMode ? "Save" : "Create Dataset"}
        </LoadingButton>
      </Box>
    </Box>
  );
};

export default AddDescriptionForm;

AddDescriptionForm.propTypes = {
  control: PropTypes.any,
  handleNextTab: PropTypes.func,
  isPending: PropTypes.bool,
  editMode: PropTypes.bool,
};
