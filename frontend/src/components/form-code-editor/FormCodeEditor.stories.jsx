import React from "react";
import { FormCodeEditor } from "./FormCodeEditor";
import { useForm } from "react-hook-form";
import { Box } from "@mui/material";

export default {
  title: "Components/FormCodeEditor",
  component: FormCodeEditor,
};

const Template = (args) => {
  const { control } = useForm();

  return (
    <Box sx={{ width: "100%" }}>
      <FormCodeEditor {...args} control={control} />
    </Box>
  );
};

export const Default = Template.bind({});

Default.args = {
  fieldName: "code",
  helperText: "Enter your code here",
  showError: true,
  height: 300,
  language: "javascript",
};

export const WithError = Template.bind({});

WithError.args = {
  fieldName: "code",
  helperText: "Error message",
  showError: true,
  height: 300,
};

export const WithoutError = Template.bind({});

WithoutError.args = {
  fieldName: "code",
  helperText: "Enter your code here",
  showError: false,
  height: 300,
};

export const WithCustomHeight = Template.bind({});

WithCustomHeight.args = {
  fieldName: "code",
  helperText: "Enter your code here",
  showError: true,
  height: 500,
  language: "javascript",
};

export const WithCustomLanguage = Template.bind({});

WithCustomLanguage.args = {
  fieldName: "code",
  helperText: "Enter your code here",
  showError: true,
  height: 300,
  language: "python",
};
