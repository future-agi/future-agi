import React from "react";
import EmptyContent from "./empty-content.jsx";

const meta = {
  component: EmptyContent,
  title: "UI Components/EmptyContent",
};

export default meta;

const Template = (args) => {
  return (
    <EmptyContent
      {...args}
      action={React.isValidElement(args.action) ? args.action : null}
    />
  );
};

export const Default = Template.bind({});
Default.args = {
  title: "No content found",
  description: "Please check back later",
};

export const WithImage = Template.bind({});
WithImage.args = {
  title: "No content found",
  description: "Please check back later",
  imgUrl: "https://via.placeholder.com/150",
};

export const WithAction = Template.bind({});
WithAction.args = {
  title: "No content found",
  description: "Please check back later",
  action: (
    <button
      style={{
        backgroundColor: "blue",
        color: "white",
        padding: 10,
        borderRadius: 5,
      }}
    >
      Add Content
    </button>
  ),
};
