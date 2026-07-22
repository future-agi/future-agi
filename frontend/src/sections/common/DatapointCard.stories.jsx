import React from "react";
import DatapointCard from "./DatapointCard";

const meta = {
  component: DatapointCard,
  title: "UI Components/DatapointCard",
};

export default meta;

const Template = (args) => {
  return <DatapointCard {...args} />;
};

export const Default = Template.bind({});

Default.args = {
  value: {
    cellValue: "This is a sample value",
  },
  column: {
    headerName: "Sample Column",
    dataType: "string",
  },
  allowCopy: true,
};

export const FloatValue = Template.bind({});

FloatValue.args = {
  value: {
    cellValue: "10.5",
  },
  column: {
    headerName: "Sample Column",
    dataType: "float",
  },
  allowCopy: true,
};

export const JsonValue = Template.bind({});

JsonValue.args = {
  value: {
    cellValue: '{"name": "Abhishek", "age": 30}',
  },
  column: {
    headerName: "Sample Column",
    dataType: "json",
  },
  allowCopy: true,
};

export const MarkdownValue = Template.bind({});

MarkdownValue.args = {
  value: {
    cellValue: "# Heading\nThis is a markdown text",
  },
  column: {
    headerName: "Sample Column",
    dataType: "string",
  },
  allowCopy: true,
};
