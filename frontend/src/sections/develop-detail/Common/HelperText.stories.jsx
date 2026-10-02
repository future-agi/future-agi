import React from "react";
import HelperText from "./HelperText";

const meta = {
  component: HelperText,
  title: "UI Components/HelperText",
};

export default meta;

const Template = (args) => {
  return <HelperText {...args} />;
};

export const Default = Template.bind({});

Default.args = {
  text: "This is a helper text",
};
