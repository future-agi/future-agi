import { Box, Button, Card } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const DatasetFilterCard = ({ addFilter }) => {
  return (
    <Card sx={{ padding: 2, border: "none", display: "flex" }}>
      <Box sx={{ flex: 1 }} />
      <Box>
        <Button
          variant="contained"
          color="primary"
          onClick={addFilter}
          startIcon={<Iconify icon="ic:round-plus" />}
          sx={{
            "& .MuiButton-startIcon": {
              margin: 0,
            },
          }}
        />
      </Box>
    </Card>
  );
};

DatasetFilterCard.propTypes = {
  addFilter: PropTypes.func.isRequired,
};

export default DatasetFilterCard;
