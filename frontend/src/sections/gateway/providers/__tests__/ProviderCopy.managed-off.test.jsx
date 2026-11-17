import React from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import AddProviderDialog from "../AddProviderDialog";
import ProviderList from "../ProviderList";

vi.mock("../hooks/useGatewayConfig", () => ({
  useUpdateProvider: () => ({ mutate: vi.fn(), isPending: false }),
  useFetchProviderModels: () => ({ mutate: vi.fn(), isPending: false }),
  useRemoveProvider: () => ({ mutate: vi.fn(), isPending: false }),
  isSavedButNotApplied: () => false,
}));

function renderDialog(provider) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AddProviderDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        provider={provider}
      />
    </QueryClientProvider>,
  );
}

describe("F1 new BYOK copy on existing provider surfaces", () => {
  it("keeps the add dialog and describes the vendor key without a pricing promise (S001)", () => {
    renderDialog();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByText("Requests use your own OpenAI key."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no gateway markup/)).toBeNull();
  });

  it("keeps the custom provider dialog without a pricing promise (S002)", async () => {
    renderDialog();
    await userEvent.click(screen.getByRole("combobox", { name: /Provider/ }));
    await userEvent.click(
      screen.getByRole("option", { name: /Custom \/ Self-hosted/ }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByText("Requests use your own provider key."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no gateway markup/)).toBeNull();
  });

  it("keeps the provider empty state and add action (S003)", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ProviderList rows={[]} canWrite onAdd={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.getByText("No providers yet")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add provider" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Add a provider with your own API key to call its models through the gateway.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no gateway markup/)).toBeNull();
  });
});
