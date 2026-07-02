import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import ModelOptions from "./ModelOptions";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Create a new QueryClient instance
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Disable retries for storybook
      retry: false,
      // Provide mock data
      staleTime: Infinity,
    },
  },
});

const meta = {
  component: ModelOptions,
  title: "UI Components/ModelOptions",
  argTypes: {
    control: {
      control: "object",
      description: "React Hook Form control object",
    },
    fieldNamePrefix: {
      control: "text",
      description: "Prefix for form field names",
    },
    hideAccordion: {
      control: "boolean",
      description: "Hide or show accordion",
    },
    setValue: {
      action: "setValue",
      description: "Function to set form value",
    },
  },
};

export default meta;

const Template = (args) => {
  const methods = useForm({
    defaultValues: {
      modelOptions: {
        temperature: 0.5,
        topP: 0.5,
        maxTokens: 1000,
        presencePenalty: 1,
        frequencyPenalty: 1,
        responseFormat: "text",
        toolChoice: "auto",
      },
    },
  });

  return (
    <QueryClientProvider client={queryClient}>
      <FormProvider {...methods}>
        <ModelOptions
          {...args}
          control={methods.control}
          setValue={methods.setValue}
          fieldNamePrefix="modelOptions"
        />
      </FormProvider>
    </QueryClientProvider>
  );
};

export const Default = {
  render: (args) => <Template {...args} />,
  args: {
    hideAccordion: false,
  },
};

export const HideAccordion = {
  render: (args) => <Template {...args} />,
  args: {
    hideAccordion: true,
  },
};
