import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import ConfiguredEvaluationType from "../ConfiguredEvaluationType/ConfiguredEvaluationType";
import EvaluationTypes from "../EvaluationType/EvaluationTypes";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    develop: {
      eval: {
        getEvalsList: (datasetId) => `/datasets/${datasetId}/evals`,
      },
    },
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

const evalListResponse = {
  data: {
    result: {
      evals: [
        {
          id: "eval-1",
          name: "Quality score",
          description: "Checks quality",
          eval_template_tags: ["LLMS"],
        },
        {
          id: "eval-2",
          name: "Missing tags is valid",
          description: "Older saved eval without tags",
        },
      ],
    },
  },
};

function renderWithQueryClient(ui) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("develop evaluation type lists", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders configured evals from canonical snake_case response fields", async () => {
    axios.get.mockResolvedValue(evalListResponse);

    renderWithQueryClient(
      <ConfiguredEvaluationType
        datasetId="dataset-1"
        onClose={() => {}}
        onOptionClick={() => {}}
      />,
    );

    expect(await screen.findByText("Quality score")).toBeInTheDocument();
    expect(screen.getByText("Missing tags is valid")).toBeInTheDocument();
    expect(screen.getByText("Llms")).toBeInTheDocument();
  });

  it("renders preset evals from canonical snake_case response fields", async () => {
    axios.get.mockResolvedValue(evalListResponse);

    renderWithQueryClient(
      <EvaluationTypes
        datasetId="dataset-1"
        onClose={() => {}}
        onOptionClick={() => {}}
      />,
    );

    expect(await screen.findByText("Quality score")).toBeInTheDocument();
    expect(screen.getByText("Missing tags is valid")).toBeInTheDocument();
    expect(screen.getByText("Llms")).toBeInTheDocument();
  });
});
