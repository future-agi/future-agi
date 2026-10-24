import React from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/api/persona/persona", () => ({
  useGetPersonasPaginated: () => ({
    data: { results: [], count: 0 },
    isLoading: false,
    isFetching: false,
  }),
  useBulkDeletePersonas: () => ({ mutate: vi.fn(), isPending: false }),
}));

const { default: PersonaListView } = await import("../PersonaListView");

// The create/edit and info drawers inside the list use React Query.
const renderList = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );

describe("PersonaListView toolbar actions", () => {
  it("renders the given actions just before Create persona", () => {
    renderList(<PersonaListView toolbarActions={<button>View Docs</button>} />);

    const docs = screen.getByRole("button", { name: "View Docs" });
    const create = screen.getByRole("button", { name: "Create persona" });
    expect(docs.parentElement).toBe(create.parentElement);
    expect(docs.nextElementSibling).toBe(create);
  });

  it("renders only Create persona when no actions are given", () => {
    renderList(<PersonaListView />);

    expect(
      screen.getByRole("button", { name: "Create persona" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View Docs" })).toBeNull();
  });
});
