import React from "react";
import DetailItem from "./drawer-detail-item.jsx";

const meta = {
  component: DetailItem,
  title: "UI Components/DetailItem",
};

export default meta;

const Template = (args) => <DetailItem {...args} />;

export const Default = Template.bind({});
Default.args = {
  title: "Item Title",
  content: "This is the item content",
};

export const HTMLContent = Template.bind({});
HTMLContent.args = {
  title: "Item Title",
  content: (
    <div>
      This is the item content with <strong>bold text</strong> and{" "}
      <em>italic text</em>
    </div>
  ),
};
