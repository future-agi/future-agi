import { Stack, Typography } from "@mui/material";
import React from "react";
import UserStatusItem from "./UserStatusItem";
import PropTypes from "prop-types";

const FailedUsersComponent = ({ failedUsers = [] }) => {
  return (
    <Stack
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        maxHeight: "200px",
        overflowY: "scroll",
      }}
      spacing={1.25}
      padding={1}
    >
      <Typography
        sx={{
          position: "sticky",
          top: 0,
          backgroundColor: "background.paper",
          zIndex: 1,
        }}
        typography={"s2_1"}
        fontWeight={"fontWeightSemiBold"}
      >
        Failed Users:
      </Typography>

      {failedUsers.map((user) => (
        <UserStatusItem
          user={{ email: user?.email, reason: user?.error }}
          status="failed"
          key={user?.email}
        />
      ))}
    </Stack>
  );
};

export default FailedUsersComponent;
FailedUsersComponent.propTypes = {
  failedUsers: PropTypes.array,
};
