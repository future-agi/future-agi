import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

// What GET /harness-environment-templates/ returns, mapped by the real adapter.
const SERVER_TEMPLATES = [
  {
    slug: "banking_support",
    name: "Banking — Card, Fraud & Account Support",
    description: "Retail-bank support with step-up auth.",
    surface: "voice",
    domain: "Fintech",
    scenario_count: 12,
    tools: [{ name: "lock_card", description: "Lock a card" }],
    rules: ["Never move money"],
    evaluations: [],
  },
  {
    slug: "debt_collection",
    name: "Collections — Payment Reminder",
    description: "Outbound early-stage collections.",
    surface: "voice",
    domain: "Financial services",
    scenario_count: 10,
    tools: [],
    rules: [],
    evaluations: [],
  },
];

vi.mock("src/api/simulate-environments/prebuilt", async () => {
  const actual = await vi.importActual("src/api/simulate-environments/prebuilt");
  return {
    ...actual,
    usePrebuiltEnvironments: () => ({
      data: SERVER_TEMPLATES.map(actual.templateToCard),
      isLoading: false,
    }),
    usePrebuiltEnvironment: () => ({ data: undefined }),
  };
});

const { default: PrebuiltEnvironmentsBrowse } = await import(
  "../PrebuiltEnvironmentsBrowse"
);

const renderBrowse = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PrebuiltEnvironmentsBrowse />
    </QueryClientProvider>,
  );
};

const tile = (name) => screen.getByRole("button", { name: new RegExp(name) });
const BANKING = "Banking — Card, Fraud & Account Support";

describe("PrebuiltEnvironmentsBrowse", () => {
  beforeEach(() => {
    navigate.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists every template under its agent-type group with its real suite size", () => {
    renderBrowse();

    expect(screen.getByText("Voice & chat")).toBeInTheDocument();
    expect(screen.getByText(BANKING)).toBeInTheDocument();
    expect(screen.getByText("Collections — Payment Reminder")).toBeInTheDocument();
    expect(screen.getByText("12 scenarios · 1 tool")).toBeInTheDocument();
  });

  it("filters templates by name or domain as the user searches", async () => {
    const user = userEvent.setup();
    renderBrowse();

    await user.type(screen.getByPlaceholderText("Search templates…"), "fintech");

    await waitFor(() =>
      expect(screen.queryByText("Collections — Payment Reminder")).not.toBeInTheDocument(),
    );
    expect(screen.getByText(BANKING)).toBeInTheDocument();
  });

  it("opens the panel for the picked template without leaving the page", async () => {
    const user = userEvent.setup();
    renderBrowse();
    expect(screen.queryByRole("button", { name: /Open template/ })).toBeNull();

    await user.click(tile(BANKING));

    expect(screen.getByRole("button", { name: /Open template/ })).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("selects a template from the keyboard (Enter)", async () => {
    renderBrowse();

    fireEvent.keyDown(tile(BANKING), { key: "Enter" });

    expect(
      await screen.findByRole("button", { name: /Open template/ }),
    ).toBeInTheDocument();
  });

  it("closes the panel when search filters its template out", async () => {
    const user = userEvent.setup();
    renderBrowse();
    await user.click(tile(BANKING));

    await user.type(screen.getByPlaceholderText("Search templates…"), "collections");

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Open template/ })).toBeNull(),
    );
  });

  it("returns to the environments home from the back button", async () => {
    const user = userEvent.setup();
    renderBrowse();

    await user.click(
      screen.getByRole("button", { name: /Back to how you want to start/ }),
    );

    expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/environments");
  });
});
