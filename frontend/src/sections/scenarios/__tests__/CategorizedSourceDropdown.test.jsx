import { fireEvent, screen, waitFor } from "@testing-library/react";
import PropTypes from "prop-types";
import React from "react";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { AGENT_TYPES } from "../../agents/constants";
import { SourceType } from "../common";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { default: CategorizedSourceDropdown } = await import(
  "../CategorizedSourceDropdown"
);

const CREATE_AGENT_DEFINITION_PATH =
  "/dashboard/simulate/agent-definitions/create-new-agent-definition";

const OPTIONS = [
  {
    label: "Support bot",
    value: "agent-1",
    sourceType: SourceType.AGENT_DEFINITION,
    agentType: AGENT_TYPES.CHAT,
  },
  {
    label: "Refund prompt",
    value: "prompt-1",
    sourceType: SourceType.PROMPT,
    agentType: "text",
  },
];

function Harness(props) {
  const { control } = useForm({ defaultValues: { sourceId: "" } });
  return (
    <CategorizedSourceDropdown
      control={control}
      fieldName="sourceId"
      label="Choose source"
      placeholder="Select agent definition or prompt"
      {...props}
    />
  );
}

Harness.propTypes = {
  options: PropTypes.array,
  isLoading: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
};

const openDropdown = (props) => {
  render(<Harness {...props} />);
  fireEvent.click(
    screen.getByPlaceholderText("Select agent definition or prompt"),
  );
};

const createButton = () =>
  screen.queryByRole("button", { name: "Create agent definition" });

describe("CategorizedSourceDropdown empty states", () => {
  beforeEach(() => {
    navigate.mockClear();
  });

  it("shows an empty state with a create action when there are no agent definitions or prompts", () => {
    openDropdown({ options: [] });

    expect(
      screen.getByText("No agent definitions or prompts yet"),
    ).toBeInTheDocument();
    expect(createButton()).toBeInTheDocument();
    expect(screen.queryByText("No results found")).not.toBeInTheDocument();
  });

  it("closes the dropdown and opens the create agent definition page from the empty state", async () => {
    openDropdown({ options: [] });

    fireEvent.click(createButton());

    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(CREATE_AGENT_DEFINITION_PATH);
    await waitFor(() => expect(createButton()).not.toBeInTheDocument());
  });

  it("keeps 'No results found' without the create action when a search matches nothing", () => {
    openDropdown({ options: OPTIONS });

    fireEvent.change(screen.getByPlaceholderText("Search..."), {
      target: { value: "zzz" },
    });

    expect(screen.getByText("No results found")).toBeInTheDocument();
    expect(createButton()).not.toBeInTheDocument();
    expect(
      screen.queryByText("No agent definitions or prompts yet"),
    ).not.toBeInTheDocument();
  });

  it("keeps 'No results found' without the create action when a category matches nothing", () => {
    openDropdown({ options: [OPTIONS[1]] });

    fireEvent.click(screen.getByText("Voice agent definition"));

    expect(screen.getByText("No results found")).toBeInTheDocument();
    expect(createButton()).not.toBeInTheDocument();
  });

  it("lists matching options with no empty state", () => {
    openDropdown({ options: OPTIONS });

    expect(screen.getByText("Support bot")).toBeInTheDocument();
    expect(screen.getByText("Refund prompt")).toBeInTheDocument();
    expect(screen.queryByText("No results found")).not.toBeInTheDocument();
    expect(createButton()).not.toBeInTheDocument();
  });

  it("does not show the empty state while sources are still loading", () => {
    openDropdown({ options: [], isLoading: true });

    expect(createButton()).not.toBeInTheDocument();
    expect(
      screen.queryByText("No agent definitions or prompts yet"),
    ).not.toBeInTheDocument();
  });
});
