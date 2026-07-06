import { Box, Link, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const InstructionTitle = ({ title, description, urltext, url, onUrlClick }) => {
  return (
    <Box
      sx={{
        width: "100%",
        gap: "2px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Typography
        color={"text.primary"}
        variant="s1"
        fontWeight={"fontWeightSemiBold"}
      >
        {title}
      </Typography>
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        <Typography
          variant="s1"
          color="text.secondary"
          fontWeight={"fontWeightRegular"}
        >
          {description}
          <Link
            onClick={onUrlClick ? onUrlClick : () => {}}
            href={url}
            target="_blank"
          >
            {" "}
            {urltext}
          </Link>
        </Typography>
      </Box>
    </Box>
  );
};

InstructionTitle.propTypes = {
  title: PropTypes.string,
  description: PropTypes.string,
  urltext: PropTypes.string,
  url: PropTypes.string,
  onUrlClick: PropTypes.func,
};

export default InstructionTitle;
