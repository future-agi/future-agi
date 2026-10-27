// src/stories/SvgColor.stories.jsx

import React from "react";
import SvgColor from "./svg-color"; // Adjust import as needed

export default {
  title: "Components/SvgColor",
  component: SvgColor,
  argTypes: {
    src: {
      control: "text",
      description: "SVG Image URL or Path",
    },
    sx: {
      control: "object",
      description: "Custom styles for the SVG",
    },
  },
};

const Template = (args) => <SvgColor {...args} />;

export const Default = Template.bind({});
Default.args = {
  src: "https://media.geeksforgeeks.org/gfg-gg-logo.svg", // Example SVG URL
  sx: { width: 50, height: 50, bgcolor: "green" }, // Custom size and color
};
