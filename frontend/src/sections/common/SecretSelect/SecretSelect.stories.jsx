import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import SecretSelect from "./SecretSelect";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const queryClient = new QueryClient();

const meta = {
  component: SecretSelect,
  title: "React Hook Form/SecretSelect",
};

export default meta;

const Template = (args) => {
  const { control } = useForm();

  return (
    <QueryClientProvider client={queryClient}>
      <FormProvider control={control}>
        <SecretSelect {...args} control={control} fieldName="secret" />
      </FormProvider>
    </QueryClientProvider>
  );
};

export const Default = Template.bind({});

Default.args = {
  label: "Select Secret",
  fullWidth: true,
  helperText: "Select a secret",
};
