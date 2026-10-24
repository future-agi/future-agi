import React from "react";
import { Box, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";

const UserHeaderCard = ({
  title,
  value,
  additional_data,
  icon,
  color,
  bgColor,
}) => {
  return (
    <Box
      display="flex"
      alignItems="center"
      gap={2}
      px={2}
      py={2}
      border="1px solid"
      borderColor="divider"
      borderRadius="4px"
      height={"100%"}
    >
      {/* Icon wrapper with light background */}
      <Box
        sx={{
          width: 40,
          height: 40,
          borderRadius: 1,
          backgroundColor: bgColor,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <SvgColor
          src={`/assets/icons/user/${icon}.svg`}
          sx={{
            width: 28,
            height: 28,
            bgcolor: color,
          }}
        />
      </Box>

      {/* Text content */}
      <Box>
        <Typography variant="s1" fontWeight={"fontWeightMedium"}>
          {title}
        </Typography>

        <Box display="flex" alignItems="flex-end" gap={1}>
          <Typography variant="m1" fontWeight={"fontWeightSemiBold"}>
            {value}
          </Typography>
          {additional_data && (
            <Typography
              variant="s2_1"
              color="text.primary"
              fontWeight={"fontWeightRegular"}
              mb={0.5}
            >
              {additional_data}
            </Typography>
          )}
        </Box>
      </Box>
    </Box>
  );
};

UserHeaderCard.propTypes = {
  title: PropTypes.string,
  value: PropTypes.string,
  icon: PropTypes.string,
  color: PropTypes.string,
  bgColor: PropTypes.string,
  additional_data: PropTypes.string,
};

export default UserHeaderCard;
