import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import HeadingAndSubHeading from "src/components/HeadingAndSubheading/HeadingAndSubheading";

function convertToTitleCase(str) {
  if (!str) {
    return "";
  }
  return str.toLowerCase().replace(/\b\w/g, (s) => s.toUpperCase());
}

const HeaderPromptRenderer = ({ messages }) => {
  const theme = useTheme();
  return (
    <Box
      display={"flex"}
      paddingTop={1}
      flexDirection={"column"}
      sx={{
        width: "100%",
      }}
    >
      <Typography
        typography={"s1"}
        color={"text.secondary"}
        fontWeight={"fontWeightBold"}
      >
        Prompt
      </Typography>
      <Box
        display={"flex"}
        flexDirection={"column"}
        overflow={"auto"}
        width={"100%"}
        minHeight={"100px"}
        maxHeight={"200px"}
        gap={theme.spacing(1)}
      >
        {messages.map((message, index) => {
          return (
            <HeadingAndSubHeading
              key={index}
              heading={
                <Typography
                  typography={"s1"}
                  fontWeight={"fontWeightMedium"}
                  color={"text.primary"}
                >
                  {convertToTitleCase(message.role)}
                </Typography>
              }
              subHeading={
                <Typography
                  typography={"s1"}
                  fontWeight={"fontWeightRegular"}
                  color={"text.primary"}
                >
                  {message.content.map((data) => {
                    if (data.type === "text") {
                      return data.text === "" ? "-" : data.text;
                    }
                  })}
                </Typography>
              }
            />
          );
        })}
      </Box>
    </Box>
  );
};

export default HeaderPromptRenderer;

HeaderPromptRenderer.propTypes = {
  messages: PropTypes.array,
};
