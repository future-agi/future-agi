import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { endpoints } from "src/utils/axios";
import { render } from "src/utils/test-utils";

import { AUTO_LOGS } from "../test-fixtures/autoLogs";
import {
  answerFrom,
  contractFailures,
  demoResponses,
  renderModelsAt,
} from "../test-fixtures/harness";
import AutoRoutingPage from "./AutoRoutingPage";
import HowAutoChooses from "./HowAutoChooses";

// The Auto routing page's structure: one Routing policy card (preference,
// candidate pool, price caps, and its save footer), the six steps of How
// auto routes a request behind disclosures, Recent decisions with its
// refresh in its own header, and Call auto last. jsdom applies no responsive
// sx, so these cases check structure, roles and text; widths are checked in
// screenshots.

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// An Owner: saving auto routing is Owner or Admin only (useGatewayAdmin).
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "http://localhost:28490/v1" },
    hosted: false,
    deploymentModeConfirmed: true,
    retryDeploymentMode: () => {},
  }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid() {
    return <div data-testid="ag-grid" />;
  }),
}));

const URL = "/dashboard/gateway/models/auto";
const FOLLOWS = Node.DOCUMENT_POSITION_FOLLOWING;

// Each step's title, and the gateway's own sentence it opens (verbatim).
const STEPS = [
  [
    "Start from the candidate pool",
    "Auto starts from your candidate pool. With none set, that is your providers' models, plus current models from major vendors in the Future AGI catalogue while Future AGI credits are on; their :free and :batch variants, models priced per request, guard, safety, moderation, embedding and image models, and models released over a year before the newest are left out.",
  ],
  [
    "Set aside models that can't serve the request",
    "Models that cannot serve the request are set aside first: missing tools, structured output or image support, a capability the request's require header asks for, a context or output limit too small, a price above your caps, a provider whose circuit is open, or a model your key or Future AGI model policy does not allow.",
  ],
  [
    "Price each model",
    "A model on your own provider is priced from your Cost Tracking price, else your Model Database price, else the gateway's model database. A model none of them prices is set aside as having no price data: give self-hosted or renamed models a price in Org Config. Future AGI catalogue models always use their catalog price.",
  ],
  [
    "Score the task and rank",
    "The gateway scores the prompt as a simple, standard or complex task. Auto ranks the models your providers serve by how well their price tier fits the task and your preference.",
  ],
  [
    "Keep two fallbacks",
    "The next two models in the ranking become fallbacks if the first one fails.",
  ],
  [
    "Record the choice",
    "Each request log records the chosen model, the task tier and the top of the ranking. The response header x-agentcc-auto-model names the model auto chose.",
  ],
];

// Where no managed models are offered, the first three sentences name none.
const OWN_ONLY = [
  "Auto starts from your candidate pool. With none set, that is your providers' models.",
  "Models that cannot serve the request are set aside first: missing tools, structured output or image support, a capability the request's require header asks for, a context or output limit too small, a price above your caps, a provider whose circuit is open, or a model your key does not allow.",
  "A model on your own provider is priced from your Cost Tracking price, else your Model Database price, else the gateway's model database. A model none of them prices is set aside as having no price data: give self-hosted or renamed models a price in Org Config.",
  STEPS[3][1],
  STEPS[4][1],
  STEPS[5][1],
];

const responses = (overrides = {}) =>
  demoResponses(endpoints, {
    [endpoints.gateway.config("default")]: {
      status: true,
      result: { routing: { auto: { preference: "balanced" } } },
    },
    [endpoints.gateway.requestLogs]: AUTO_LOGS,
    ...overrides,
  });

const open = (r = responses()) => {
  api.get.mockImplementation(answerFrom(r));
  return renderModelsAt(URL, <AutoRoutingPage />);
};

const panelOf = (button) =>
  document.getElementById(button.getAttribute("aria-controls"));

const follows = (a, b) => Boolean(a.compareDocumentPosition(b) & FOLLOWS);

afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
});

beforeEach(() => {
  api.get.mockReset();
});

describe("Auto routing page (layout)", () => {
  it("reads in order: Routing policy, How auto routes a request, Recent decisions, Call auto", async () => {
    open();
    const policy = await screen.findByRole("region", {
      name: "Routing policy",
    });
    const how = screen.getByRole("region", {
      name: "How auto routes a request",
    });
    const recent = screen.getByRole("region", { name: "Recent decisions" });
    const call = screen.getByRole("region", { name: "Call auto" });
    expect(follows(policy, how)).toBe(true);
    expect(follows(how, recent)).toBe(true);
    expect(follows(recent, call)).toBe(true);
  });

  it("holds preference, candidate pool and price caps in one Routing policy card, parted by dividers", async () => {
    open();
    const card = await screen.findByRole("region", { name: "Routing policy" });
    const preference = await within(card).findByRole("group", {
      name: "Preference",
    });
    const pool = within(card).getByRole("group", { name: "Candidate pool" });
    const caps = within(card).getByRole("group", { name: "Price caps" });
    expect(
      within(preference).getByRole("group", {
        name: "Auto routing preference",
      }),
    ).toBeInTheDocument();
    expect(within(preference).getByText(/route-hint/)).toBeInTheDocument();
    expect(within(pool).getByLabelText("Candidate models")).toBeInTheDocument();
    expect(within(caps).getByLabelText("Max input price")).toBeInTheDocument();
    expect(within(caps).getByLabelText("Max output price")).toBeInTheDocument();
    expect(follows(preference, pool)).toBe(true);
    expect(follows(pool, caps)).toBe(true);
    // One card: its parts are not cards (regions) of their own.
    expect(within(card).queryAllByRole("region")).toHaveLength(0);
    expect(
      within(card).getAllByRole("separator").length,
    ).toBeGreaterThanOrEqual(2);
  });

  it("ends the card with the save footer: state, Discard and Save changes after the price caps", async () => {
    open();
    const card = await screen.findByRole("region", { name: "Routing policy" });
    const quality = await within(card).findByRole("button", {
      name: /^Quality/,
    });
    await waitFor(() => expect(quality).toBeEnabled());
    await userEvent.click(quality);
    const caps = within(card).getByRole("group", { name: "Price caps" });
    const unsaved = within(card).getByText("Unsaved changes");
    const discard = within(card).getByRole("button", { name: "Discard" });
    const save = within(card).getByRole("button", { name: "Save changes" });
    expect(save).toBeEnabled();
    for (const el of [unsaved, discard, save]) {
      expect(caps.contains(el)).toBe(false);
      expect(follows(caps, el)).toBe(true);
    }
    expect(follows(unsaved, discard)).toBe(true);
    expect(follows(discard, save)).toBe(true);
  });

  it("marks each preference tile with a hidden themed radio and keeps the tile named by its label", async () => {
    open();
    const group = await screen.findByRole("group", {
      name: "Auto routing preference",
    });
    await waitFor(() =>
      expect(
        within(group).getByRole("button", { name: /^Balanced/ }),
      ).toHaveAttribute("aria-pressed", "true"),
    );
    // The radios are the tiles' marks, not extra controls.
    expect(within(group).queryAllByRole("radio")).toHaveLength(0);
    const tiles = ["Cost", "Balanced", "Quality"].map((name) =>
      within(group).getByRole("button", { name: new RegExp(`^${name}`) }),
    );
    const marks = tiles.map((tile) =>
      tile.querySelector('input[type="radio"]'),
    );
    marks.forEach((mark) => {
      expect(mark).not.toBeNull();
      expect(mark.closest('[aria-hidden="true"]')).not.toBeNull();
      expect(mark).toHaveAttribute("tabindex", "-1");
    });
    expect(marks.map((mark) => mark.checked)).toEqual([false, true, false]);
    await userEvent.click(tiles[2]);
    expect(tiles[2]).toHaveAttribute("aria-pressed", "true");
    expect(marks.map((mark) => mark.checked)).toEqual([false, false, true]);
  });

  it("offers auto in the page header with a copy button", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    try {
      open();
      await screen.findByRole("heading", { level: 1, name: "Auto routing" });
      const copy = screen.getByRole("button", { name: 'Copy "auto"' });
      expect(copy.closest("section")).toBeNull();
      expect(copy.parentElement).toHaveTextContent(/^auto$/);
      fireEvent.click(copy);
      expect(writeText).toHaveBeenCalledWith("auto");
    } finally {
      delete navigator.clipboard;
    }
  });

  it("puts Refresh decisions and the bounded-sample note in the Recent decisions header", async () => {
    open();
    const recent = await screen.findByRole("region", {
      name: "Recent decisions",
    });
    // Once the sample has loaded: its grid, and the note's load time.
    const grid = await within(recent).findByTestId("ag-grid");
    const heading = within(recent).getByRole("heading", {
      name: "Recent decisions",
    });
    const refresh = within(recent).getByRole("button", {
      name: "Refresh decisions",
    });
    expect(within(recent).getByText(/latest 200 requests only/)).toBe(
      within(recent).getByText(/Sample last loaded/),
    );
    expect(follows(heading, refresh)).toBe(true);
    expect(follows(refresh, grid)).toBe(true);
    expect(
      within(recent).getByRole("region", { name: "Candidates" }),
    ).toBeInTheDocument();
    expect(
      within(recent).getByRole("region", { name: "Chosen most often" }),
    ).toBeInTheDocument();
  });

  // A ranking carries float noise (0.00025089999999999997): costs under a
  // cent read to 4 significant digits, a cent and up to 2 decimals, and only
  // an estimate is marked "≈".
  it("shows a tiny cost to 4 significant digits, without float noise", async () => {
    open();
    const candidates = await screen.findByRole("region", {
      name: "Candidates",
    });
    const ranked = within(candidates).getByRole("table", {
      name: "Ranked candidates",
    });
    expect(
      within(ranked).getByText("anthropic/claude-haiku-5.5").closest("tr"),
    ).toHaveTextContent("≈ $0.0002509");
    expect(
      within(ranked)
        .getByText("mistralai/voxtral-small-24b-2507")
        .closest("tr"),
    ).toHaveTextContent("≈ $0.0001509");
    expect(ranked.textContent).not.toMatch(/0\.000[12]50899/);
    expect(candidates).toHaveTextContent("· $0.000003");
    expect(candidates).not.toHaveTextContent("≈ $0.000003");
  });

  it("shows a cost of a cent or more to 2 decimals", async () => {
    const decision = {
      model: "gpt-4o",
      provider: "openai",
      task_tier: "complex",
      preference: "quality",
      considered: 2,
      ranked: [
        {
          model: "gpt-4o",
          provider: "openai",
          tier: 0.9,
          score: 0.9,
          est_cost_usd: 12.3456,
        },
        {
          model: "gpt-4o-mini",
          provider: "openai",
          tier: 0.2,
          score: 0.5,
          est_cost_usd: 0.0417,
        },
      ],
      rejected: [],
      fallbacks: ["gpt-4o-mini"],
    };
    open(
      responses({
        [endpoints.gateway.requestLogs]: {
          count: 1,
          results: [
            {
              id: "cents-1",
              request_id: "01CENTSAUTO",
              model: "gpt-4o",
              provider: "openai",
              started_at: "2026-10-24T10:00:00Z",
              latency_ms: 12,
              cost: "1.5",
              status_code: 200,
              is_error: false,
              metadata: {
                auto_model: "gpt-4o",
                auto_requested: "auto",
                auto_task_tier: "complex",
                auto_preference: "quality",
                auto_considered: "2",
                auto_decision: JSON.stringify(decision),
                billing_mode: "byok",
              },
            },
          ],
        },
      }),
    );
    const candidates = await screen.findByRole("region", {
      name: "Candidates",
    });
    const { rows } = within(candidates).getByRole("table", {
      name: "Ranked candidates",
    });
    expect(rows[1]).toHaveTextContent("≈ $12.35");
    expect(rows[2]).toHaveTextContent("≈ $0.04");
    expect(candidates).toHaveTextContent("· $1.50");
    expect(candidates).not.toHaveTextContent("≈ $1.50");
  });

  // A request's own cost is the log's exact decimal, never rounded.
  it("shows the decision's own cost exactly", async () => {
    open(
      responses({
        [endpoints.gateway.requestLogs]: {
          count: 1,
          results: [{ ...AUTO_LOGS.results[0], cost: "0.0149" }],
        },
      }),
    );
    const candidates = await screen.findByRole("region", {
      name: "Candidates",
    });
    expect(candidates).toHaveTextContent("· $0.0149");
    expect(candidates).not.toHaveTextContent(/· \$0\.01(?!\d)/);
    expect(candidates).not.toHaveTextContent("≈ $0.0149");
  });

  it("lets the empty state jump to the example request", async () => {
    const scrolled = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = vi.fn(function scrollIntoView() {
      scrolled.push(this);
    });
    try {
      open(
        responses({
          [endpoints.gateway.requestLogs]: { count: 0, results: [] },
        }),
      );
      const recent = await screen.findByRole("region", {
        name: "Recent decisions",
      });
      await within(recent).findByText("No auto-routed requests yet");
      expect(
        within(recent).getByRole("button", { name: "Refresh decisions" }),
      ).toBeInTheDocument();
      await userEvent.click(
        within(recent).getByRole("button", { name: "See the example request" }),
      );
      const call = screen.getByRole("region", { name: "Call auto" });
      expect(scrolled).toContain(call);
      expect(document.activeElement).toBe(
        within(call).getByRole("heading", { name: "Call auto" }),
      );
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});

describe("How auto routes a request (layout)", () => {
  it("titles each of the six steps and opens its full sentence on demand", async () => {
    render(<HowAutoChooses managedOffered />);
    const how = screen.getByRole("region", {
      name: "How auto routes a request",
    });
    const toggles = within(how).getAllByRole("button");
    expect(toggles).toHaveLength(STEPS.length);
    for (const [title, line] of STEPS) {
      const toggle = within(how).getByRole("button", { name: title });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      await userEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      expect(panelOf(toggle).textContent).toBe(line);
      await userEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "false");
    }
  });

  it("opens each step's own sentence where no managed models are offered", async () => {
    render(<HowAutoChooses managedOffered={false} />);
    const how = screen.getByRole("region", {
      name: "How auto routes a request",
    });
    const toggles = within(how).getAllByRole("button");
    expect(toggles).toHaveLength(OWN_ONLY.length);
    for (const [i, toggle] of toggles.entries()) {
      await userEvent.click(toggle);
      expect(panelOf(toggle).textContent).toBe(OWN_ONLY[i]);
    }
  });

  it("starts with the first step open on a wide screen, and every step closed on a narrow one", () => {
    const original = window.matchMedia;
    window.matchMedia = vi.fn((query) => ({
      matches: /min-width:\s*1200px/.test(query),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }));
    try {
      const { unmount } = render(<HowAutoChooses managedOffered />);
      const wide = within(
        screen.getByRole("region", { name: "How auto routes a request" }),
      ).getAllByRole("button");
      expect(wide.map((b) => b.getAttribute("aria-expanded"))).toEqual([
        "true",
        "false",
        "false",
        "false",
        "false",
        "false",
      ]);
      unmount();
    } finally {
      window.matchMedia = original;
    }
    render(<HowAutoChooses managedOffered />);
    const narrow = within(
      screen.getByRole("region", { name: "How auto routes a request" }),
    ).getAllByRole("button");
    expect(
      narrow.every((b) => b.getAttribute("aria-expanded") === "false"),
    ).toBe(true);
  });
});
