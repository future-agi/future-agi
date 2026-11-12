import React from "react";
import { Box, Button, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const BackButton = ({ text = "", onBack }) => {
  const navigate = useNavigate();

  const handleBack = () => {
    if (onBack) {
      onBack();
    } else {
      navigate("/dashboard/projects/observe");
    }
  };

  return (
    <Box display="flex" alignItems="center" borderRadius={"4px"} gap="4px">
      <Button
        color="inherit"
        size="small"
        startIcon={
          <Iconify
            icon="solar:arrow-left-linear"
            width={16}
            height={16}
            color={"text.primary"}
          />
        }
        onClick={handleBack}
        variant="outlined"
        sx={{
          color: "text.primary",
          typography: "s1",
          border: "1px solid",
          borderColor: "divider",
          "& .MuiButton-startIcon": {
            marginRight: "4px",
          },
        }}
      >
        Back
      </Button>

      <Typography
        variant="m1"
        color={"text.primary"}
        fontWeight="fontWeightBold"
      >
        {text}
      </Typography>
    </Box>
  );
};

BackButton.propTypes = {
  text: PropTypes.string,
  onBack: PropTypes.func,
};

export default BackButton;
