import React from "react";
import AddedEvaluationCard from "./AddedEvaluationCard";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";

const queryClient = new QueryClient();

const meta = {
  component: AddedEvaluationCard,
  title: "UI Components/AddedEvaluationCard",
};

export default meta;

const Template = (args) => {
  const params = useParams();

  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/dataset/${params.dataset}`]}>
        <Routes>
          <Route
            path="/dataset/:dataset"
            element={<AddedEvaluationCard dataset={params.dataset} {...args} />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

export const Default = Template.bind({});

Default.args = {
  setSelectedUserEvalList: () => {},
  selectedUserEvalList: [],
  userEval: {
    id: 1,
    name: "Evaluation 1",
    description: "This is evaluation 1",
  },
  selected: true,
  onChange: () => {},
  viewConfig: {
    checkbox: true,
    run: true,
  },
  requiredKeys: "key1, key2",
  model: "Model 1",
  name: "Evaluation 1",
  description: "This is evaluation 1",
  onRemove: () => {},
  experimentEval: {
    experimentId: 1,
  },
  refreshGrid: () => {},
};

export const WithoutCheckbox = Template.bind({});

WithoutCheckbox.args = {
  setSelectedUserEvalList: () => {},
  selectedUserEvalList: [],
  userEval: {
    id: 1,
    name: "Evaluation 1",
    description: "This is evaluation 1",
  },
  selected: true,
  onChange: () => {},
  viewConfig: {
    checkbox: false,
    run: true,
  },
  requiredKeys: "key1, key2",
  model: "Model 1",
  name: "Evaluation 1",
  description: "This is evaluation 1",
  onRemove: () => {},
  experimentEval: {
    experimentId: 1,
  },
  refreshGrid: () => {},
};

export const WithoutRun = Template.bind({});

WithoutRun.args = {
  setSelectedUserEvalList: () => {},
  selectedUserEvalList: [],
  userEval: {
    id: 1,
    name: "Evaluation 1",
    description: "This is evaluation 1",
  },
  selected: true,
  onChange: () => {},
  viewConfig: {
    checkbox: true,
    run: false,
  },
  requiredKeys: "key1, key2",
  model: "Model 1",
  name: "Evaluation 1",
  description: "This is evaluation 1",
  onRemove: () => {},
  experimentEval: {
    experimentId: 1,
  },
  refreshGrid: () => {},
};
