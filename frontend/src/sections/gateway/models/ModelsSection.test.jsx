import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { endpoints } from "src/utils/axios";

import ModelsSection from "./ModelsSection";
import {
  MANAGED_ON,
  answerFrom,
  contractFailures,
  demoResponses,
  renderModelsAt,
} from "./test-fixtures/harness";

// The Models pages around their content: which page each Models URL opens
// for what the deployment offers, and who may write. The Models pages are
// the catalog (/models), Auto routing (/models/auto) and a model page; the
// left rail links them, where the page used to have a Catalog and an Auto
// routing tab.

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
// The page's write controls follow the gateway admin role check
// (useGatewayAdmin): Owner (15) and Admin (8) write, Member (3) and Viewer
// (1) read.
let mockOrgLevel = 15;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "https://gw.example.com/v1" },
  }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid() {
    return <div data-testid="ag-grid" />;
  }),
}));

// What the console's managed-models status returns on a deployment whose
// gateway has no managed provider, which is every hosted org today
// (agentcc/views/managed_models.py).
const NOT_OFFERED = {
  available: false,
  enabled: false,
  balance_usd: null,
  markup_percent: null,
};
const CATALOG_DESCRIPTION =
  "Every model in the gateway's catalog, what it costs, and how your organization can call it.";
const ORG_MODELS_DESCRIPTION =
  "Models configured through your own providers. Open a model to inspect its route, try a request or get API code.";

const responses = (status, overrides = {}) =>
  demoResponses(endpoints, {
    [endpoints.gateway.managedModels]: status,
    [endpoints.gateway.config("default")]: {
      status: true,
      result: { routing: { auto: { preference: "balanced" } } },
    },
    ...overrides,
  });

const open = (url, r) => {
  api.get.mockImplementation(answerFrom(r));
  return renderModelsAt(url, <ModelsSection />);
};

const catalogRead = () =>
  api.get.mock.calls.some(
    ([url]) => url === endpoints.gateway.managedModelsCatalog,
  );
const heading = (name) => screen.findByRole("heading", { level: 1, name });

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  mockOrgLevel = 15;
});

// Every request the pages sent must pass the API contract.
afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
});

// BP-04, round19/product-10x-1109/BUILD-PLAN.json:256,278-286:
// available:false keeps Models browsable through the organization's providers;
// Auto routing remains reachable, without a managed catalog or credit offer.
describe("ModelsSection without managed models", () => {
  const notOffered = () => responses({ status: true, result: NOT_OFFERED });

  it("opens Your models without redirecting or offering a managed catalog", async () => {
    const { location } = open("/dashboard/gateway/models", notOffered());

    expect(await heading("Your models")).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models");
    expect(
      await screen.findByRole("textbox", { name: "Search your models" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Provider" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(CATALOG_DESCRIPTION)).toBeNull();
    expect(catalogRead()).toBe(false);
  });

  it("shows no managed models card", async () => {
    open("/dashboard/gateway/models", notOffered());

    expect(await heading("Your models")).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "Future AGI credits" }),
    ).toBeNull();
    expect(screen.queryByText(/Not available on this deployment/)).toBeNull();
    expect(screen.queryByText(/No managed catalog/)).toBeNull();
    expect(screen.queryByText(/No Future AGI catalogue/)).toBeNull();
    expect(screen.queryByLabelText("Future AGI credits")).toBeNull();
  });

  it("describes the organization's own-provider models, not the managed catalog", async () => {
    open("/dashboard/gateway/models", notOffered());

    expect(await screen.findByText(ORG_MODELS_DESCRIPTION)).toBeInTheDocument();
    expect(screen.queryByText(CATALOG_DESCRIPTION)).toBeNull();
  });

  it("opens Your models for a link to the catalog tab", async () => {
    const { location } = open(
      "/dashboard/gateway/models/catalog?q=1",
      notOffered(),
    );

    expect(await heading("Your models")).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models");
    expect(location.search).toBe("?q=1");
  });

  it("keeps Auto routing reachable without managed catalog models", async () => {
    const { location } = open("/dashboard/gateway/models", notOffered());

    expect(await heading("Your models")).toBeInTheDocument();
    const autoLink = screen.getByRole("link", { name: "Auto routing" });
    expect(autoLink).toHaveAttribute("href", "/dashboard/gateway/models/auto");
    await userEvent.click(autoLink);
    expect(await heading("Auto routing")).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models/auto");
    expect(
      await screen.findByText(
        "Restrict auto to these models. Leave empty for the default pool: your providers' models.",
      ),
    ).toBeInTheDocument();
    const input = screen.getByLabelText("Candidate models");
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.click(input);
    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).queryByText("Future AGI catalogue")).toBeNull();
    expect(catalogRead()).toBe(false);
  });
});

describe("ModelsSection for a user who is not an Owner or Admin", () => {
  it.each([
    ["a Member", 3],
    ["a Viewer", 1],
    ["a user whose level is not loaded", null],
  ])(
    "%s gets a read-only page: no auto routing or managed-models writes",
    async (_who, level) => {
      mockOrgLevel = level;
      const { unmount } = open(
        "/dashboard/gateway/models",
        responses({ status: true, result: MANAGED_ON }),
      );
      const toggle = await screen.findByRole("checkbox", {
        name: "Future AGI credits",
      });
      expect(toggle).toBeDisabled();
      expect(
        toggle.closest('[aria-label="Owner or Admin only"]'),
      ).not.toBeNull();
      unmount();

      open(
        "/dashboard/gateway/models/auto",
        responses({ status: true, result: MANAGED_ON }),
      );
      expect(
        await screen.findByText("You need edit access to change auto routing."),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^Cost/ })).toBeDisabled();
      expect(screen.getByLabelText("Max input price")).toBeDisabled();
    },
  );
});

describe("ModelsSection with managed models offered", () => {
  const offered = () => responses({ status: true, result: MANAGED_ON });

  it("shows the card and opens the catalog, with Auto routing beside it", async () => {
    const { location } = open("/dashboard/gateway/models", offered());

    expect(await heading("Models")).toBeInTheDocument();
    expect(
      await screen.findByRole("checkbox", { name: "Future AGI credits" }),
    ).toBeInTheDocument();
    expect(screen.getByText(CATALOG_DESCRIPTION)).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models");
    await waitFor(() => expect(catalogRead()).toBe(true));
  });

  it("opens Auto routing by its URL, with the managed catalog as candidates", async () => {
    open("/dashboard/gateway/models/auto", offered());

    expect(await heading("Auto routing")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Restrict auto to these models. Leave empty for the default pool: your providers' models, plus current models from major vendors in the Future AGI catalogue while Future AGI credits are on.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(catalogRead()).toBe(true));
  });
});

describe("ModelsSection before the status is known", () => {
  it("guesses nothing while the status loads", async () => {
    const { location } = open(
      "/dashboard/gateway/models",
      responses(() => new Promise(() => {})),
    );

    expect(await heading("Models")).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models");
    expect(screen.queryByText(/No managed catalog/)).toBeNull();
    expect(screen.queryByText(/No Future AGI catalogue/)).toBeNull();
    expect(
      screen.queryByRole("checkbox", { name: "Future AGI credits" }),
    ).toBeNull();
    expect(catalogRead()).toBe(false);
  });

  it("keeps the retry, and Auto routing open, when the status fails", async () => {
    const failing = () => responses(new Error("boom"));
    const { location, unmount } = open("/dashboard/gateway/models", failing());

    expect(
      await screen.findByText("Couldn't load the model catalog"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models");
    expect(catalogRead()).toBe(false);
    unmount();

    open("/dashboard/gateway/models/auto", failing());
    expect(await heading("Auto routing")).toBeInTheDocument();
    expect(
      await screen.findByText(/Couldn.t load the Future AGI catalogue/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Candidate models")).toBeInTheDocument();
  });
});
