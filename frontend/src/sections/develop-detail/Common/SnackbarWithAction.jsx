import React from "react";
import { styled } from "@mui/system";
import { Box, Button, Typography } from "@mui/material";
import PropTypes from "prop-types";

const StyledSnackbarBox = styled(Box)(() => ({
  display: "flex",
  alignItems: "center",
}));

const StyledSnackbarButton = styled(Button)(({ theme }) => ({
  background: "transparent",
  border: "none",
  padding: "7px 8px",
  fontSize: "13px",
  fontWeight: 500,
  lineHeight: "22px",
  letterSpacing: "0.02em",
  color: theme.palette.status.success.fg,
  textDecoration: "underline",
  boxShadow: "none",
  "&:hover": {
    background: "transparent",
    border: "none",
    boxShadow: "none",
    textDecoration: "underline",
  },
}));

const SnackbarWithAction = ({ message, buttonText, onClick }) => {
  return (
    <StyledSnackbarBox>
      <Typography variant="s1" fontWeight={"fontWeightMedium"}>
        {message}
      </Typography>
      <StyledSnackbarButton
        size="small"
        variant="contained"
        onClick={() => onClick()}
      >
        {buttonText}
      </StyledSnackbarButton>
    </StyledSnackbarBox>
  );
};

SnackbarWithAction.propTypes = {
  message: PropTypes.string,
  buttonText: PropTypes.string,
  onClick: PropTypes.func,
};

export default SnackbarWithAction;
