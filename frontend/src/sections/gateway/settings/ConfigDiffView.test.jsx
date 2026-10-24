import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ConfigDiffView from "./ConfigDiffView";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    gateway: {
      orgConfig: {
        diff: (id) => `/agentcc/org-configs/${id}/diff/`,
      },
    },
  },
}));

const TARGET = { id: "cfg-2", version: 2 };
const ACTIVE = { id: "cfg-4", version: 4 };
const EMPTY = { added: {}, removed: {}, changed: {} };

function diffWith(sections) {
  return { from_version: 4, to_version: 2, routing: EMPTY, ...sections };
}

function renderView(props = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ConfigDiffView
        open
        target={TARGET}
        active={ACTIVE}
        onConfirm={onConfirm}
        onCancel={onCancel}
        isActivating={false}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onConfirm, onCancel };
}

describe("ConfigDiffView", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("compares the version against the active one and lists what changes", async () => {
    get.mockResolvedValue({
      data: {
        result: diffWith({
          routing: {
            added: { strategy: "cost" },
            removed: { auto: { preference: "cost" } },
            changed: { failover: { old: { enabled: true }, new: {} } },
          },
        }),
      },
    });

    renderView();

    expect(await screen.findByText("routing")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/agentcc/org-configs/cfg-2/diff/", {
      params: { compare_to: "cfg-4" },
    });
    expect(screen.getByText(/\+ strategy: "cost"/)).toBeInTheDocument();
    expect(screen.getByText(/- auto:/)).toBeInTheDocument();
    expect(screen.getByText(/~ failover:/)).toBeInTheDocument();
  });

  it.each([
    [
      "changed",
      {
        added: {},
        removed: {},
        changed: { enabled: { old: true, new: false } },
      },
      /turns managed models off/i,
    ],
    [
      "removed",
      { added: {}, removed: { enabled: true }, changed: {} },
      /turns managed models off/i,
    ],
    [
      "added",
      { added: { enabled: true }, removed: {}, changed: {} },
      /turns managed models on/i,
    ],
  ])(
    "warns when managed_models.enabled is %s",
    async (_, managedModels, message) => {
      get.mockResolvedValue({
        data: { result: diffWith({ managed_models: managedModels }) },
      });

      renderView();

      expect(await screen.findByText(message)).toBeInTheDocument();
    },
  );

  it("does not warn when managed models stay as they are", async () => {
    get.mockResolvedValue({
      data: {
        result: diffWith({
          managed_models: {
            added: { allow_data_collection: false },
            removed: {},
            changed: {},
          },
        }),
      },
    });

    renderView();

    expect(await screen.findByText("managed_models")).toBeInTheDocument();
    expect(screen.queryByText(/turns managed models/i)).not.toBeInTheDocument();
  });

  it("shows warnings the server sends with the diff", async () => {
    get.mockResolvedValue({
      data: {
        result: diffWith({
          warnings: [
            { code: "budget_removed", message: "Budgets are removed." },
          ],
        }),
      },
    });

    renderView();

    expect(await screen.findByText("Budgets are removed.")).toBeInTheDocument();
  });

  it("says so when the version changes nothing", async () => {
    get.mockResolvedValue({ data: { result: diffWith({}) } });

    renderView();

    expect(
      await screen.findByText(/same settings as the active version/i),
    ).toBeInTheDocument();
  });

  it("activates only when confirmed", async () => {
    const user = userEvent.setup();
    get.mockResolvedValue({ data: { result: diffWith({}) } });

    const { onConfirm, onCancel } = renderView();
    await screen.findByText(/same settings/i);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Activate v2" }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it("still lets a rollback go ahead when the diff cannot be loaded, warning from the versions", async () => {
    const user = userEvent.setup();
    get.mockRejectedValue({ statusCode: 500 });

    const { onConfirm } = renderView({
      target: { ...TARGET, managed_models: {} },
      active: { ...ACTIVE, managed_models: { enabled: true } },
    });

    expect(
      await screen.findByText(/could not load the changes/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/turns managed models off/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Activate v2" }));
    expect(onConfirm).toHaveBeenCalled();
  });

  it("warns from the versions when the diff has no managed_models section", async () => {
    get.mockResolvedValue({ data: { result: diffWith({}) } });

    renderView({
      target: { ...TARGET, managed_models: { enabled: true } },
      active: { ...ACTIVE, managed_models: {} },
    });

    expect(
      await screen.findByText(/turns managed models on/i),
    ).toBeInTheDocument();
  });

  it("does not warn on a failed diff when the versions agree", async () => {
    get.mockRejectedValue({ statusCode: 500 });

    renderView({
      target: { ...TARGET, managed_models: { enabled: true } },
      active: { ...ACTIVE, managed_models: { enabled: true } },
    });

    expect(
      await screen.findByText(/could not load the changes/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/turns managed models/i)).not.toBeInTheDocument();
  });

  it("does not ask for a diff when no version is active", async () => {
    renderView({ active: null });

    expect(
      screen.getByText(/no active version to compare against/i),
    ).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Activate v2" })).toBeEnabled();
  });
});
