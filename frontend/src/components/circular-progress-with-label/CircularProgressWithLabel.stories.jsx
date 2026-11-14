import React from "react";
import CircularProgressWithLabel from "./CircularProgressWithLabel";

const meta = {
  component: CircularProgressWithLabel,
  title: "UI Components/CircularProgressWithLabel",
};

export default meta;

const Template = (args) => <CircularProgressWithLabel {...args} />;

export const Default = Template.bind({});
Default.args = {
  value: 25,
  color: "primary.main",
};

export const CustomColor = Template.bind({});
CustomColor.args = {
  value: 25,
  color: "error.main",
};
