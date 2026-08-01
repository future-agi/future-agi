import React from "react";
import Label from "./label";
import Iconify from "../iconify";

const meta = {
  component: Label,
  title: "UI Components/Label",
};

export default meta;

const Template = (args) => {
  if (
    args.startIcon &&
    typeof args.startIcon === "object" &&
    !args.startIcon.type
  ) {
    args.startIcon = <div>{}</div>;
  }

  if (args.endIcon && typeof args.endIcon === "object" && !args.endIcon.type) {
    args.endIcon = <div></div>;
  }

  return <Label {...args} />;
};

export const Default = Template.bind({});
Default.args = {
  children: "Label",
  variant: "soft",
  color: "default",
  startIcon: null,
  endIcon: null,
  sx: {},
};

export const FilledVariant = Template.bind({});
FilledVariant.args = {
  children: "Label",
  variant: "filled",
  color: "primary",
  sx: {},
};

export const OutlinedVariant = Template.bind({});
OutlinedVariant.args = {
  children: "Label",
  variant: "outlined",
  color: "secondary",
  sx: {},
};

export const GhostVariant = Template.bind({});
GhostVariant.args = {
  children: "Label",
  variant: "ghost",
  color: "info",
  sx: {},
};

export const WithStartIcon = Template.bind({});
WithStartIcon.args = {
  children: "Label",
  variant: "soft",
  color: "default",
  startIcon: (
    <Iconify icon="mdi:apple-keyboard-command" height={16} width={16} />
  ),
  endIcon: null,
  sx: {},
};

export const WithEndIcon = Template.bind({});
WithEndIcon.args = {
  children: "Label",
  variant: "soft",
  color: "default",
  startIcon: null,
  endIcon: <Iconify icon="mdi:apple-keyboard-command" height={16} width={16} />,
  sx: {},
};

export const WithBothIcons = Template.bind({});
WithBothIcons.args = {
  children: "Label",
  variant: "soft",
  color: "default",
  startIcon: (
    <Iconify icon="mdi:apple-keyboard-command" height={16} width={16} />
  ),
  endIcon: <Iconify icon="mdi:apple-keyboard-command" height={16} width={16} />,
  sx: {},
};

export const CustomSx = Template.bind({});
CustomSx.args = {
  children: "Label",
  variant: "soft",
  color: "default",
  startIcon: null,
  endIcon: null,
  sx: {
    backgroundColor: "red",
    color: "white",
  },
};
