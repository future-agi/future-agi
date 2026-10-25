import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import AutoRoutingTab from "./AutoRoutingTab";
import { CATALOG_MODELS } from "./test-fixtures/catalog";

const { post, get, snackbars, config } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
  // The org config the gateway config read returns; tests replace routing.
  config: { current: {} },
}));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/gateway/${id}/config`,
      updateConfig: (id) => `/gateway/${id}/config/update`,
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["gpt-4o", "my-own-model"],
}));

const renderTab = (props = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          mutationCache: new MutationCache({ onError: () => {} }),
          defaultOptions: {
            mutations: { retry: false },
            queries: { retry: false },
          },
        })
      }
    >
      <AutoRoutingTab
        gatewayId="gw-1"
        canWrite
        catalogModels={CATALOG_MODELS}
        managedStatus={{ available: true, enabled: true }}
        baseUrl="https://gw.example.com/v1"
        {...props}
      />
    </QueryClientProvider>,
  );

const savedBody = () => post.mock.calls[0][1];
const preference = (name) =>
  screen.getByRole("button", { name: new RegExp(`^${name}`) });

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  snackbars.length = 0;
  config.current = { routing: { strategy: "cost" } };
  get.mockImplementation(async () => ({
    data: { status: true, result: config.current },
  }));
  post.mockResolvedValue({
    data: { status: true, result: { version: 3, gateway_synced: true } },
  });
});

describe("AutoRoutingTab", () => {
  it("starts from the gateway default when nothing is saved", async () => {
    renderTab();
    await waitFor(() =>
      expect(preference("Balanced")).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByLabelText("Max input price")).toHaveValue("");
    expect(screen.getByRole("button", { name: /Save changes/ })).toBeDisabled();
  });

  it("saves only what was set when there is no saved auto config", async () => {
    renderTab();
    await waitFor(() => expect(preference("Cost")).toBeEnabled());

    await userEvent.click(preference("Cost"));
    await userEvent.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe("/gateway/gw-1/config/update");
    expect(savedBody()).toEqual({ routing: { auto: { preference: "cost" } } });
    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message: "Auto routing saved",
        options: { variant: "success" },
      }),
    );
  });

  it("reports a save the gateway did not apply, never as saved", async () => {
    post.mockResolvedValue({
      data: {
        status: true,
        result: {
          version: 3,
          gateway_synced: false,
          gateway_warning: "Gateway sync failed.",
        },
      },
    });
    renderTab();
    await waitFor(() => expect(preference("Cost")).toBeEnabled());

    await userEvent.click(preference("Cost"));
    await userEvent.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message:
          "Saved, but the gateway did not apply it: Gateway sync failed.",
        options: { variant: "warning" },
      }),
    );
    expect(snackbars).not.toContainEqual(
      expect.objectContaining({ options: { variant: "success" } }),
    );
  });

  it("loads saved settings and saves edits to them", async () => {
    config.current = {
      routing: {
        auto: {
          preference: "cost",
          candidates: ["anthropic/claude-sonnet-5.5"],
          max_input_per_mtok: 3,
        },
      },
    };
    renderTab();
    await waitFor(() =>
      expect(preference("Cost")).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByText("anthropic/claude-sonnet-5.5")).toBeInTheDocument();
    expect(screen.getByLabelText("Max input price")).toHaveValue("3");

    await userEvent.click(preference("Quality"));
    await userEvent.type(screen.getByLabelText("Max output price"), "20");
    await userEvent.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(savedBody()).toEqual({
      routing: {
        auto: {
          preference: "quality",
          candidates: ["anthropic/claude-sonnet-5.5"],
          max_input_per_mtok: 3,
          max_output_per_mtok: 20,
        },
      },
    });
  });

  it("clears saved candidates and caps with null", async () => {
    config.current = {
      routing: {
        auto: {
          preference: "balanced",
          candidates: ["gpt-4o"],
          max_input_per_mtok: 3,
        },
      },
    };
    renderTab();
    const chip = await screen.findByRole("button", { name: "gpt-4o" });
    fireEvent.keyDown(chip, { key: "Backspace" });
    await userEvent.clear(screen.getByLabelText("Max input price"));
    await userEvent.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(savedBody()).toEqual({
      routing: {
        auto: {
          preference: "balanced",
          candidates: null,
          max_input_per_mtok: null,
        },
      },
    });
  });

  it("offers your providers' models and the managed catalog as candidates", async () => {
    renderTab();
    const input = await screen.findByLabelText("Candidate models");
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.click(input);

    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByText("Your providers")).toBeInTheDocument();
    expect(within(listbox).getByText("Managed catalog")).toBeInTheDocument();
    await userEvent.click(within(listbox).getByText("openai/gpt-5-mini"));
    await userEvent.click(screen.getByRole("button", { name: /Save changes/ }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(savedBody().routing.auto.candidates).toEqual(["openai/gpt-5-mini"]);
  });

  it("blocks a save with an invalid cap", async () => {
    renderTab();
    const field = await screen.findByLabelText("Max input price");
    await waitFor(() => expect(field).toBeEnabled());
    await userEvent.type(field, "0");
    expect(screen.getByText(/Enter an amount above \$0/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save changes/ })).toBeDisabled();
  });

  it("discards edits", async () => {
    renderTab();
    await waitFor(() => expect(preference("Quality")).toBeEnabled());
    await userEvent.click(preference("Quality"));
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(preference("Balanced")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("is read-only without write access", async () => {
    renderTab({ canWrite: false });
    await waitFor(() =>
      expect(
        screen.getByText("You need edit access to change auto routing."),
      ).toBeInTheDocument(),
    );
    expect(preference("Cost")).toBeDisabled();
    expect(screen.getByLabelText("Max input price")).toBeDisabled();
  });

  it("shows a usage example with model auto", async () => {
    renderTab();
    expect(await screen.findByText(/"model": "auto"/)).toBeInTheDocument();
  });

  // Each sentence matches the gateway: routing/auto.go ChooseAuto (the score
  // starts from a 0-1 fit of the price tier to the task tier and the
  // preference) and server/auto_route.go recordAutoDecision (the log keeps
  // the top five ranked; x-agentcc-auto-model is the choice, set before any
  // fallback). The copy describes only the ranking and the log.
  it("explains the ranking without overstating it", async () => {
    renderTab();
    const howItWorks = (await screen.findByText("How auto works")).closest(
      ".MuiCard-root",
    );

    expect(howItWorks).toHaveTextContent(
      "The gateway scores the prompt as a simple, standard or complex task. Auto ranks the models your providers serve by how well their price tier fits the task and your preference.",
    );
    expect(howItWorks).toHaveTextContent(
      "Each request log records the chosen model, the task tier and the top of the ranking. The response header x-agentcc-auto-model names the model auto chose.",
    );
    expect(howItWorks).not.toHaveTextContent(/near-ties/);
    expect(howItWorks).not.toHaveTextContent(/5%/);
    expect(howItWorks).not.toHaveTextContent(/failing/);
    expect(howItWorks).not.toHaveTextContent(/names the model used/);
  });

  it("describes the default pool with the managed catalog when it is offered", async () => {
    renderTab();

    expect(
      await screen.findByText(
        /plus current models from major vendors in the managed catalog while managed models are on/,
      ),
    ).toBeInTheDocument();
  });

  it("describes the default pool as your providers' models when managed models are not offered", async () => {
    renderTab({
      catalogModels: undefined,
      managedStatus: {
        available: false,
        enabled: false,
        balance_usd: null,
        markup_percent: null,
      },
    });

    expect(
      await screen.findByText(
        "Restrict auto to these models. Leave empty for the default pool: your providers' models.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/managed catalog/)).toBeNull();
  });
});
