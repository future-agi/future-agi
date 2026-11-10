import { Box, Chip, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
const STATUS_MAP = {
  SUCCESS: "success",
  FAILED: "failed",
};
const UserStatusItem = ({ user, status }) => {
  return (
    <Box
      sx={{
        border: "1px solid var(--border-light)",
        borderRadius: "4px",
        padding: 1,
        display: "flex",
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: 1,
        }}
      >
        <ShowComponent condition={status === STATUS_MAP.FAILED}>
          <SvgColor
            sx={{ width: "14px", color: "status.danger.fg" }}
            src="/assets/icons/ic_critical.svg"
          />
        </ShowComponent>
        <Stack spacing={0.25}>
          <Typography typography={"s1"} fontWeight={"fontWeightRegular"}>
            {user.email}
          </Typography>
          <ShowComponent condition={status === STATUS_MAP.FAILED}>
            <Typography
              typography={"s2"}
              fontWeight={"fontWeightRegular"}
              color="status.danger.fg"
            >
              {user.reason}
            </Typography>
          </ShowComponent>
        </Stack>
        <ShowComponent condition={status === STATUS_MAP.SUCCESS}>
          <Chip
            label={"Added"}
            icon={
              <SvgColor
                sx={{ width: 16, color: "status.success.fg" }}
                src="/assets/icons/ic_completed.svg"
              />
            }
            sx={{
              bgcolor: "transparent",
              color: "status.success.fg",
              "& .MuiChip-icon": {
                color: "status.success.fg",
              },
              ":hover": {
                backgroundColor: "transparent",
              },
            }}
          />
        </ShowComponent>
      </Box>
      <ShowComponent condition={status === STATUS_MAP.FAILED}>
        <Chip
          label={"Failed"}
          icon={
            <SvgColor
              sx={{ width: 14, color: "status.danger.fg" }}
              src="/assets/icons/ic_critical.svg"
            />
          }
          sx={{
            color: "status.danger.fg",
            backgroundColor: "transparent",
            "& .MuiChip-icon": {
              color: "status.danger.fg",
            },
            ":hover": {
              backgroundColor: "transparent",
            },
          }}
        />
      </ShowComponent>
      <ShowComponent condition={status === STATUS_MAP.SUCCESS}>
        <Chip
          size="medium"
          label={"Email Sent"}
          icon={
            <SvgColor sx={{ width: 16 }} src="/assets/icons/ic_email.svg" />
          }
          sx={{
            bgcolor: "status.success.bg",
            color: "status.success.fg",
            px: 1,
            "& .MuiChip-icon": {
              color: "status.success.fg",
            },
            ":hover": {
              backgroundColor: "status.success.bg",
            },
          }}
        />
      </ShowComponent>
    </Box>
  );
};

export default UserStatusItem;
UserStatusItem.propTypes = {
  user: PropTypes.object.isRequired,
  status: PropTypes.string.isRequired,
};
