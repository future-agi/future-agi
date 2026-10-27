import { Box, Typography, Link, Stack, Collapse } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const CreateScenarioHeader = ({
  title,
  description,
  rightSection,
  docLink,
}) => {
  return (
    <Box
      sx={{ display: "flex", justifyContent: "space-between", width: "100%" }}
    >
      <Box sx={{ width: "100%" }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
          gap="4px"
        >
          <Typography typography="m3" fontWeight="fontWeightMedium">
            {title}
          </Typography>
          <Collapse in={!!rightSection}>{rightSection}</Collapse>
        </Stack>
        <Box display="flex" alignItems="center" gap="4px">
          <Typography typography="s2_1" color="text.secondary">
            {description}
          </Typography>
          {docLink && (
            <Link
              href={docLink.url}
              target="_blank"
              rel="noopener noreferrer"
              sx={docLink.sx}
            >
              Learn more
            </Link>
          )}
        </Box>
      </Box>
    </Box>
  );
};

CreateScenarioHeader.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
  rightSection: PropTypes.node,
  docLink: PropTypes.object,
};

export default CreateScenarioHeader;
