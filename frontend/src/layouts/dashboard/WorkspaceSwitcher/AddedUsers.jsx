import { Stack, Typography } from "@mui/material";
import React from "react";
import UserStatusItem from "./UserStatusItem";
import PropTypes from "prop-types";

const AddedUsers = ({ addedUsers }) => {
  return (
    <Stack
      sx={{
        border: "1px solid",
        maxHeight: "200px",
        overflowY: "scroll",
        borderColor: "divider",
        borderRadius: "4px",
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
        Added Users:
      </Typography>

      {addedUsers?.map((user) => (
        <UserStatusItem
          user={{ email: user, reason: "" }}
          status="success"
          key={user}
        />
      ))}
    </Stack>
  );
};

export default AddedUsers;
AddedUsers.propTypes = {
  addedUsers: PropTypes.array,
};
