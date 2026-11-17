import { Box, Typography } from "@mui/material";
import React from "react";

const descriptions = [
  "Write a prompt in the left column, then click Run Prompt to see the response",
  "Editing the prompt, or changing model parameters creates a new version",
  "Write variables like this: {{VARIABLE_NAME}}",
  "Click Add Message to add more turns and simulate a conversation",
  "Good examples improve responses. Add them as user and assistant messages",
];

const DefaultOutput = () => {
  return (
    <Box
      sx={{
        display: "flex",
        gap: "16px",
        flexDirection: "column",
        "& > ul > li::marker": {
          fontSize: "12px",
          color: "text.tertiary",
        },
        width: "100%",
      }}
    >
      <Typography
        variant="m3"
        fontWeight={"fontWeightSemiBold"}
        color="text.primary"
      >
        Output
      </Typography>
      {/* <PromptLoading /> */}
      <ul style={{ paddingLeft: "24px", margin: "0" }}>
        {descriptions?.map((item, index) => (
          <li key={index} style={{ margin: 0, padding: 0, lineHeight: 0 }}>
            <Typography
              typography="m3"
              fontWeight={"fontWeightRegular"}
              color="text.secondary"
              fontFamily={"Inter, sans-serif"}
            >
              {item}
            </Typography>
          </li>
        ))}
      </ul>
    </Box>
  );
};

export default DefaultOutput;
