import React from "react";
import { MemoryRouter } from "react-router-dom";
import Logo from "./logo";

const meta = {
  title: "UI Components/Logo",
  component: Logo,
  parameters: {
    controls: {
      expanded: true,
    },
  },
  argTypes: {
    collapsed: {
      control: "boolean",
    },
    disabledLink: {
      control: "boolean",
    },
  },
};

export default meta;

export const Default = (args) => (
  <MemoryRouter>
    <Logo {...args} />
  </MemoryRouter>
);
