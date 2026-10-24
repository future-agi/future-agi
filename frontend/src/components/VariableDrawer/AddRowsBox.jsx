import {
  Box,
  Button,
  FormHelperText,
  TextField,
  Typography,
} from "@mui/material";
import React, { useState } from "react";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import { useSnackbar } from "src/components/snackbar";

const AddRowsBox = ({ handleAddRows, currentRows }) => {
  const { enqueueSnackbar } = useSnackbar();
  const [rowCount, setRowCount] = useState(0); // rowCount as a number from the start

  const wouldExceedLimit = currentRows + rowCount > 10;
  const maxAllowedRows = 10 - currentRows;

  const handleAddRowMethod = () => {
    if (rowCount > maxAllowedRows) {
      enqueueSnackbar(`You can only add up to ${maxAllowedRows} row(s)`, {
        variant: "error",
      });
      return;
    }
    handleAddRows(rowCount);
    setRowCount(0);
  };

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        paddingX: 2,
        paddingY: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Button
          variant="contained"
          color="primary"
          size="small"
          sx={{ width: "80px" }}
          startIcon={
            <SvgColor src="/assets/icons/components/ic_add.svg" width="16px" />
          }
          onClick={handleAddRowMethod}
          disabled={currentRows === 10 || wouldExceedLimit || rowCount === 0}
        >
          Add
        </Button>

        <TextField
          type="number"
          value={rowCount}
          onChange={(e) => setRowCount(Number(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleAddRowMethod();
            }
          }}
          sx={{
            width: "100px",
            "& .MuiInputBase-input": {
              height: "30px",
              paddingY: "2px",
            },
            "& .MuiInputLabel-root": {
              fontSize: "12px",
            },
          }}
          placeholder="Row count"
          label="No.of rows"
          size="small"
          disabled={currentRows === 10}
          error={wouldExceedLimit && rowCount > 0}
          inputProps={{ min: 0 }}
        />

        <Typography variant="s1">More rows</Typography>
      </Box>

      {wouldExceedLimit && rowCount > 0 && (
        <FormHelperText error sx={{ margin: 0, fontSize: "12px" }}>
          Maximum {maxAllowedRows} rows can be added (total limit: 10 rows)
        </FormHelperText>
      )}
    </Box>
  );
};

AddRowsBox.propTypes = {
  handleAddRows: PropTypes.func,
  currentRows: PropTypes.number,
};

export default AddRowsBox;
