import React from "react";
import PageHeadings from "./PageHeadings";

export default {
  title: "Components/PageHeadings",
  component: PageHeadings,
  argTypes: {
    title: { control: "text" },
    description: { control: "text" },
  },
};

const Template = (args) => <PageHeadings {...args} />;

export const Default = Template.bind({});
Default.args = {
  title: "Page Title",
  description: "This is a short description of the page.",
};

export const TitleOnly = Template.bind({});
TitleOnly.args = {
  title: "Only Title",
  description: "",
};

export const DescriptionOnly = Template.bind({});
DescriptionOnly.args = {
  title: "",
  description: "Only description is provided without a title.",
};

export const Empty = Template.bind({});
Empty.args = {
  title: "",
  description: "",
};
