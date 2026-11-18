import React from "react";
import Image from "./image";
import { Box, Typography } from "@mui/material";

const meta = {
  component: Image,
  title: "UI Components/Image",
};

export default meta;

const Template = (args) => {
  return (
    <Box
      sx={{
        width: "100%",
        maxWidth: "400px",
        margin: "0 auto",
        padding: "20px",
        textAlign: "center",
      }}
    >
      <Typography variant="h6" gutterBottom>
        Image Component Example
      </Typography>
      <Image {...args} />
    </Box>
  );
};

export const Default = Template.bind({});

Default.args = {
  alt: "Example Image",
  src: "https://via.placeholder.com/400x300",
  ratio: "16/9",
  overlay: "rgba(0, 0, 0, 0.4)",
};

export const NoOverlay = Template.bind({});

NoOverlay.args = {
  alt: "Example Image",
  src: "https://via.placeholder.com/400x300",
  ratio: "16/9",
  overlay: undefined,
};

export const DifferentRatio = Template.bind({});

DifferentRatio.args = {
  alt: "Example Image",
  src: "https://via.placeholder.com/400x300",
  ratio: "4/3",
};

export const DisabledEffect = Template.bind({});

DisabledEffect.args = {
  alt: "Example Image",
  src: "https://via.placeholder.com/400x300",
  ratio: "16/9",
  disabledEffect: true,
};

export const CustomEffect = Template.bind({});

CustomEffect.args = {
  alt: "Example Image",
  src: "https://via.placeholder.com/400x300",
  ratio: "16/9",
  effect: "opacity",
};
