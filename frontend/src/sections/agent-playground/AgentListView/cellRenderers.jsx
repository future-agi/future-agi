import React from "react";
import { Box, Avatar, AvatarGroup, Typography } from "@mui/material";
import { format } from "date-fns";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import SvgColor from "src/components/svg-color";
import _ from "lodash";

// Name Cell Renderer
export const NameCellRenderer = (params) => {
  const { value } = params;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.5,
        px: 2,
        height: "100%",
      }}
    >
      <Typography
        typography="s2"
        fontWeight="fontWeightMedium"
        color="text.primary"
      >
        {value}
      </Typography>
    </Box>
  );
};

NameCellRenderer.propTypes = {
  value: PropTypes.string,
};

// Date Cell Renderer
export const DateCellRenderer = (params) => {
  const { value } = params;

  if (!value) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          px: 2,
          height: "100%",
        }}
      >
        <Typography typography="s2" color="text.disabled">
          -
        </Typography>
      </Box>
    );
  }

  try {
    const date = new Date(value);
    const formattedDateTime = format(date, "dd-MM-yyyy, h:mm a");

    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          px: 2,
          height: "100%",
        }}
      >
        <Typography typography="s2" color="text.primary">
          {formattedDateTime}
        </Typography>
      </Box>
    );
  } catch {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          px: 2,
          height: "100%",
        }}
      >
        <Typography typography="s2" color="text.disabled">
          -
        </Typography>
      </Box>
    );
  }
};

DateCellRenderer.propTypes = {
  value: PropTypes.string,
};

// Collaborators Cell Renderer
export const CollaboratorsCellRenderer = (params) => {
  const { value } = params;

  if (!value || value.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          px: 2,
          height: "100%",
        }}
      >
        <Typography typography="s2" color="text.secondary">
          -
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        px: 2,
        height: "100%",
      }}
    >
      <AvatarGroup
        sx={{
          "& .MuiAvatar-root": {
            width: 24,
            height: 24,
            fontSize: 12,
          },
        }}
      >
        {value.map((collaborator, index) => (
          <CustomTooltip
            size="small"
            arrow
            key={index}
            title={collaborator?.email}
            show
          >
            <Avatar
              alt={collaborator?.name}
              sx={{
                width: 24,
                height: 24,
                fontSize: 12,
              }}
            >
              {_.toUpper(collaborator?.name?.[0])}
            </Avatar>
          </CustomTooltip>
        ))}
      </AvatarGroup>
    </Box>
  );
};

CollaboratorsCellRenderer.propTypes = {
  value: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      email: PropTypes.string,
    }),
  ),
};

// Header Component
export const HeaderComponent = (params) => {
  const { iconSrc, label } = params;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        height: "100%",
      }}
    >
      {iconSrc && (
        <SvgColor
          src={iconSrc}
          sx={{
            width: iconSrc.includes("ic_col_header") ? 20 : 16,
            height: iconSrc.includes("ic_col_header") ? 20 : 16,
            color: iconSrc.includes("ic_col_header")
              ? "text.primary"
              : "text.secondary",
          }}
        />
      )}
      <Typography typography="s2" fontWeight="fontWeightSemiBold">
        {label}
      </Typography>
    </Box>
  );
};

HeaderComponent.propTypes = {
  iconSrc: PropTypes.string,
  label: PropTypes.string.isRequired,
};
