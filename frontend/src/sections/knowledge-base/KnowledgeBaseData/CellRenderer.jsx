import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { format } from "date-fns";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { statusIcons } from "../sheet-view/icons";

const cellStyles = {
  Processing: {
    backgroundColor: "status.info.bg",
    color: "status.info.fg",
  },
  Failed: {
    backgroundColor: "status.danger.bg",
    color: "status.danger.fg",
  },
  Completed: {
    backgroundColor: "status.success.bg",
    color: "status.success.fg",
  },
};

export const ProcessingStatusCell = ({ value, data }) => {
  const status = value === "PartialCompleted" ? "Processing" : value;
  const statusIconSrc = statusIcons[status];

  return (
    <Box
      sx={{
        height: "100%",
        width: "fit-content",
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
      }}
    >
      <Stack
        sx={{
          px: "12px",
          py: "4px",
          gap: "8px",
          height: "24px",
          borderRadius: "12px",
          ...cellStyles[status],
        }}
        direction={"row"}
        alignItems={"center"}
      >
        <Box
          component={"img"}
          sx={{
            height: "12px",
            width: "12px",
          }}
          alt="document icon"
          src={statusIconSrc}
        />
        <Typography variant="s2" fontWeight={"fontWeightMedium"}>
          {status}
        </Typography>
        {status === "Failed" && (
          <CustomTooltip show={true} title={data?.error || ""} arrow>
            <Iconify
              icon="solar:info-circle-outline"
              color="text.primary"
              width={12}
              height={12}
            />
          </CustomTooltip>
        )}
      </Stack>
    </Box>
  );
};

ProcessingStatusCell.propTypes = {
  value: PropTypes.string,
  data: PropTypes.object,
};

export const DateFormat = ({ value }) => {
  const newData = value
    ? format(new Date(value), "dd/MM/yyyy, HH:mm aaa")
    : value;
  return (
    <Box
      sx={{
        height: "100%",
        width: "100%",
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
      }}
    >
      {newData}
    </Box>
  );
};

DateFormat.propTypes = {
  value: PropTypes.string,
};
