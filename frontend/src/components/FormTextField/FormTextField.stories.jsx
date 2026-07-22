import React from "react";
import { useForm } from "react-hook-form";
import { FormTextField } from "./FormTextField";

export default {
  title: "Components/FormTextField",
  component: FormTextField,
  parameters: {
    layout: "centered",
  },
};

const FormWrapper = ({ children, defaultValues = {} }) => {
  const methods = useForm({
    defaultValues,
  });

  return children(methods);
};

// Basic text field
export const Basic = {
  render: () => (
    <FormWrapper defaultValues={{ basic: "" }}>
      {(methods) => (
        <FormTextField
          control={methods.control}
          fieldName="basic"
          label="Basic Input"
          placeholder="Enter text"
          helperText=""
          defaultValue=""
          isSpinnerField={false}
          onBlur={() => {}}
          required={false}
        />
      )}
    </FormWrapper>
  ),
};

// Required field
export const Required = {
  render: () => (
    <FormWrapper defaultValues={{ required: "" }}>
      {(methods) => (
        <FormTextField
          control={methods.control}
          fieldName="required"
          label="Required Input"
          required={true}
          placeholder="This field is required"
          helperText=""
          defaultValue=""
          isSpinnerField={false}
          onBlur={() => {}}
        />
      )}
    </FormWrapper>
  ),
};

// Number field with spinner
export const NumberSpinner = {
  render: () => (
    <FormWrapper defaultValues={{ number: 1 }}>
      {(methods) => (
        <FormTextField
          control={methods.control}
          fieldName="number"
          label="Number Input"
          isSpinnerField={true}
          fieldType="number"
          placeholder="Enter a number"
          helperText=""
          defaultValue={1}
          onBlur={() => {}}
          required={false}
        />
      )}
    </FormWrapper>
  ),
};

// With helper text
export const WithHelperText = {
  render: () => (
    <FormWrapper defaultValues={{ helper: "" }}>
      {(methods) => (
        <FormTextField
          control={methods.control}
          fieldName="helper"
          label="Input with Helper"
          helperText="This is a helpful message"
          placeholder="Enter text"
          defaultValue=""
          isSpinnerField={false}
          onBlur={() => {}}
          required={false}
        />
      )}
    </FormWrapper>
  ),
};

// With error state
export const WithError = {
  render: () => (
    <FormWrapper defaultValues={{ error: "" }}>
      {(methods) => (
        <FormTextField
          control={methods.control}
          fieldName="error"
          label="Error Input"
          error={true}
          helperText="This field has an error"
          placeholder="Error state"
          defaultValue=""
          isSpinnerField={false}
          onBlur={() => {}}
          required={false}
        />
      )}
    </FormWrapper>
  ),
};
