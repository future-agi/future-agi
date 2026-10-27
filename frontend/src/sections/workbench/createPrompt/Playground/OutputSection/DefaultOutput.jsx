import { Box, Typography } from "@mui/material";
import React from "react";

const descriptions = [
  "Write a prompt in the left column, and click run to see the response",
  "Editing the prompt, or changing model parameters creates a new version",
  "Write variables like this: {{VARIABLE_NAME}}",
  "Add messages using to simulate a conversation",
  "High quality examples greatly improve performance. After drafting a prompt, click EXAMPLES to add some",
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
          color: "text.disabled",
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
