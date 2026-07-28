import React from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DevelopDatasetSelect from "./DevelopDatasetSelect";

const queryClient = new QueryClient();

const meta = {
  component: DevelopDatasetSelect,
  title: "UI Components/DevelopDatasetSelect",
};

const Template = (args) => {
  const [value, setValue] = React.useState("dataset1");
  const options = [
    { value: "dataset1", label: "Dataset 1" },
    { value: "dataset2", label: "Dataset 2" },
    { value: "dataset3", label: "Dataset 3" },
  ];

  const optionsToUse =
    args.options && typeof args.options.find === "function"
      ? args.options
      : options;

  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DevelopDatasetSelect
          options={optionsToUse}
          value={value}
          setValue={setValue}
          {...args}
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

export const Default = Template.bind({});

Default.args = {
  options: [],
};

export const WithValue = Template.bind({});

WithValue.args = {
  value: "dataset2",
};

export const WithOptions = Template.bind({});

WithOptions.args = {
  options: [
    { value: "dataset1", label: "Dataset 1" },
    { value: "dataset2", label: "Dataset 2" },
    { value: "dataset3", label: "Dataset 3" },
    { value: "dataset4", label: "Dataset 4" },
  ],
};

export default meta;
