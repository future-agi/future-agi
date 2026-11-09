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
import HowAutoChooses from "./auto/HowAutoChooses";
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

  it("reports a save the gateway did not apply, never as saved (NG-34)", async () => {
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
    expect(
      within(listbox).getByText("Future AGI catalogue"),
    ).toBeInTheDocument();
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

  // Auto never offers a catalog model managed credits cannot buy, nor one
  // priced per request (auto sets it aside as having no price data); each of
  // your providers' models says how auto prices it.
  it("offers no catalog model auto could never pick, and says how own models are priced", async () => {
    renderTab();
    const input = await screen.findByLabelText("Candidate models");
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.click(input);
    const listbox = await screen.findByRole("listbox");
    // Priced per request: auto has no price card to rank it by.
    expect(
      within(listbox).queryByText("perplexity/sonar-deep-research"),
    ).not.toBeInTheDocument();
    expect(
      within(listbox).getByText("anthropic/claude-sonnet-5.5"),
    ).toBeInTheDocument();
    expect(
      within(listbox).getAllByText(
        /No price from your org: auto skips it unless the gateway's model database prices this model/,
      ).length,
    ).toBeGreaterThan(0);
    expect(
      within(listbox).queryByText(/picked only when the gateway can price it/),
    ).not.toBeInTheDocument();
  });

  it("describes the default pool with the managed catalog when it is offered", async () => {
    renderTab();

    expect(
      await screen.findByText(
        /plus current models from major vendors in the Future AGI catalogue while Future AGI credits are on/,
      ),
    ).toBeInTheDocument();
    // The explanation is in HowAutoChooses, not this settings tab; its
    // complete Org Config/price assertion lives in that section's case below.
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
    expect(screen.queryByText(/Future AGI catalogue/)).toBeNull();
    expect(screen.queryByText(/managed/i)).toBeNull();
  });
});

// How auto chooses is its own section on the Auto routing page, above the
// settings this tab holds. Each sentence matches the gateway: routing/auto.go
// ChooseAuto (the score starts from a 0-1 fit of the price tier to the task
// tier and the preference) and server/auto_route.go recordAutoDecision (the
// log keeps the top five ranked; x-agentcc-auto-model is the choice, set
// before any fallback). The copy promises no health handling: ChooseAuto's
// latency and error deductions read figures every org shares for a provider
// id (HealthMonitor), and a candidate whose recorded calls all failed takes no
// error deduction (auto.go: SuccessRate > 0).
describe("How auto chooses", () => {
  it("explains the ranking without overstating it", () => {
    render(<HowAutoChooses managedOffered />);
    const howItWorks = screen.getByRole("region", {
      name: "How auto chooses",
    });

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

  it("names the managed catalog in the default pool only where it is offered", () => {
    const { unmount } = render(<HowAutoChooses managedOffered={false} />);
    const pool = screen.getByRole("region", { name: "How auto chooses" });
    expect(pool).toHaveTextContent(
      "Auto starts from your candidate pool. With none set, that is your providers' models.",
    );
    expect(pool).not.toHaveTextContent(/from major vendors/);
    unmount();

    render(<HowAutoChooses managedOffered />);
    expect(
      screen.getByRole("region", { name: "How auto chooses" }),
    ).toHaveTextContent(
      "plus current models from major vendors in the Future AGI catalogue while Future AGI credits are on",
    );
  });

  // A deployment that offers no managed models has no managed policy and no
  // managed catalog price to explain.
  it("mentions no managed models where none are offered", () => {
    render(<HowAutoChooses managedOffered={false} />);
    const how = screen.getByRole("region", { name: "How auto chooses" });
    expect(how).not.toHaveTextContent(/managed/i);
    expect(how).toHaveTextContent(
      "a provider whose circuit is open, or a model your key does not allow.",
    );
    expect(how).toHaveTextContent(
      "give self-hosted or renamed models a price in Org Config.",
    );
  });

  it("keeps the managed policy and the catalog price where managed models are offered", () => {
    render(<HowAutoChooses managedOffered />);
    const how = screen.getByRole("region", { name: "How auto chooses" });
    expect(how).toHaveTextContent(
      "a model your key or Future AGI model policy does not allow.",
    );
    expect(how).toHaveTextContent(
      "Org Config. Future AGI catalogue models always use the Future AGI price.",
    );
    expect(how).not.toHaveTextContent(/managed/i);
  });
});
