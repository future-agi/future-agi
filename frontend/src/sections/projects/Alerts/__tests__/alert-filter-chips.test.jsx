import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { useForm } from "react-hook-form";

import AlertFilterBar from "../components/AlertFilterBar";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { result: [] } }) },
  endpoints: { project: { getEvalAttributeList: () => "/eval-attributes" } },
}));

const Harness = ({ filters }) => {
  const { control, setValue } = useForm({ defaultValues: { filters } });
  return (
    <AlertFilterBar control={control} setValue={setValue} projectId="p1" />
  );
};

Harness.propTypes = { filters: PropTypes.array };

const renderBar = (filters) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Harness filters={filters} />
    </QueryClientProvider>,
  );

describe("alert filter chips", () => {
  it("does not apply span-type labels to attribute values", () => {
    // "agent" is a span type AND a plausible attribute value. Only the span
    // type row may render it as "Agent".
    renderBar([
      {
        property: "observationType",
        propertyId: "",
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: "llm",
        },
      },
      {
        property: "attributes",
        propertyId: "deployment_env",
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: "agent",
        },
      },
    ]);

    expect(screen.getByText("LLM")).toBeInTheDocument();
    expect(screen.getByText("agent")).toBeInTheDocument();
    expect(screen.queryByText("Agent")).not.toBeInTheDocument();
  });
});
