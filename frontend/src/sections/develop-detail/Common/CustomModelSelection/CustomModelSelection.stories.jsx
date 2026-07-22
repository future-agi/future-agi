import React from "react";
import { CustomModelSelection } from "./CustomModelSelection";
import { useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const queryClient = new QueryClient();

const meta = {
  component: CustomModelSelection,
  title: "React Hook Form/CustomModelSelection",
};

export default meta;

const Template = (args) => {
  const { control } = useForm({
    defaultValues: {
      model: "",
    },
  });

  return (
    <QueryClientProvider client={queryClient}>
      <CustomModelSelection
        control={control}
        fieldName="model"
        label="Select a model"
        fullWidth
        dropDownMaxHeight={300}
        {...args}
      />
    </QueryClientProvider>
  );
};

export const Default = Template.bind({});

Default.args = {
  fieldName: "model",
  valueSelector: undefined,
  helperText: "",
  fullWidth: true,
  dropDownMaxHeight: 300,
  customMenuItem: undefined,
  onConfigOpen: undefined,
};
