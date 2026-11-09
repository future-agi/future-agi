import { Box, Button } from "@mui/material";
import React from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const ApiKeyBar = ({ searchQuery, setSearchQuery, handleCreateClick }) => {
  return (
    <Box
      sx={{
        display: "flex",
        gap: 1,
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <FormSearchField
        size="small"
        placeholder="Search"
        sx={{
          minWidth: "250px",
          "& .MuiOutlinedInput-root": { height: "30px" },
        }}
        searchQuery={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />
      {handleCreateClick ? (
        <Box>
          <Button
            onClick={handleCreateClick}
            variant="contained"
            color="primary"
            startIcon={
              <Iconify
                icon="octicon:plus-24"
                color="background.paper"
                sx={{
                  width: "20px",
                  height: "20px",
                }}
              />
            }
            sx={{ px: "24px", typography: "s1" }}
          >
            Add API Key
          </Button>
        </Box>
      ) : null}
    </Box>
  );
};

ApiKeyBar.propTypes = {
  searchQuery: PropTypes.string,
  setSearchQuery: PropTypes.func,
  handleCreateClick: PropTypes.func,
};

export default ApiKeyBar;
