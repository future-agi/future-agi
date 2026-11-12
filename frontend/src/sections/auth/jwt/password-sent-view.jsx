import { Stack } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import PasswordSentLogin from "./PasswordSentView.jsx/PasswordSentLogin";

const PasswordSentView = ({
  email,
  emailLabel,
  setRegisterSuccess,
  isSubmitting,
  errorMsg,
  password,
}) => {
  return (
    <Stack spacing={2} sx={{ mb: 3, alignItems: "center", minHeight: 350 }}>
      <PasswordSentLogin
        email={email}
        emailLabel={emailLabel}
        setRegisterSuccess={setRegisterSuccess}
        password={password}
        errorMsg={errorMsg}
        isSubmitting={isSubmitting}
      />
    </Stack>
  );
};

export default PasswordSentView;

PasswordSentView.propTypes = {
  email: PropTypes.string,
  emailLabel: PropTypes.string,
  setRegisterSuccess: PropTypes.func,
  isSubmitting: PropTypes.bool,
  errorMsg: PropTypes.string,
  password: PropTypes.object,
};
