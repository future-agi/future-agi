import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { packStats } from "../../helpers/packStats";

const navigate = vi.fn();
const adoptMutate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/simulate-environments/environments", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/environments",
  );
  return { ...actual, useAdoptTemplate: () => ({ mutate: adoptMutate }) };
});

const { default: TemplateBuildPanel } = await import("../TemplateBuildPanel");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);

const TEMPLATE = {
  id: "env-voice-support",
  name: "Customer Support Line",
  surface: "voice",
  tagline: "Inbound phone support for an online storefront",
  difficulty: "Starter",
  seed: {
    tables: [
      { name: "orders", rows: 500, note: "delayed" },
      { name: "customers", rows: 200, note: "loyalty" },
      { name: "returns", rows: 85, note: "outside window" },
      { name: "products", rows: 340, note: "discontinued" },
    ],
  },
  tools: [
    { name: "lookup_order", desc: "x" },
    { name: "issue_refund", desc: "x" },
    { name: "escalate", desc: "x" },
  ],
  rules: [
    "Refunds above $200 need approval",
    "Never confirm identity by phone",
  ],
  evalPreset: ["task_success", "tone"],
};

const renderPanel = (props = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TemplateBuildPanel template={TEMPLATE} {...props} />
    </QueryClientProvider>,
  );
};

describe("TemplateBuildPanel", () => {
  beforeEach(() => {
    resetEnvironmentsStore();
    navigate.mockReset();
    adoptMutate.mockClear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders both build-mode tabs", () => {
    renderPanel();
    expect(
      screen.getByRole("tab", { name: /Preview here/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /Build locally/ }),
    ).toBeInTheDocument();
  });

  it("explains what the local draft does and how to start a real build", () => {
    renderPanel();
    expect(
      screen.getByText(/template's data, tools and hard rules/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/no agent is connected/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        "To build a cloud environment, open the Build environment tab in Simulate and connect your own agent or source.",
      ),
    ).toBeInTheDocument();
  });

  it("computes the stats card from the template", () => {
    renderPanel();
    const rows = TEMPLATE.seed.tables.reduce((a, t) => a + t.rows, 0);
    expect(
      screen.getByText(`${rows.toLocaleString()} sample rows`),
    ).toBeInTheDocument();
    expect(screen.getByText("3 tool definitions")).toBeInTheDocument();
    expect(screen.getByText("2 rule definitions")).toBeInTheDocument();
    expect(
      screen.getByText(`${packStats(TEMPLATE).scenarios} draft scenarios`),
    ).toBeInTheDocument();
    expect(screen.getByText("2 suggested")).toBeInTheDocument();
    expect(
      screen.getByText("Sample baseline (not connected)"),
    ).toBeInTheDocument();
  });

  it("shows the local-only notice before the action", () => {
    renderPanel();
    expect(screen.getByText(/Local preview only/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        "Opening a draft creates no cloud environment, sandbox or simulation run.",
      ),
    ).toBeInTheDocument();
  });

  it("opens the template draft without registering or navigating to a server environment", async () => {
    const user = userEvent.setup();
    renderPanel();

    const button = screen.getByRole("button", { name: /Open local draft/ });
    expect(button).toBeEnabled();
    await user.click(button);

    expect(adoptMutate).toHaveBeenCalledWith(TEMPLATE);
    const { workspaceEnvs, byEnv } = useEnvironmentsStore.getState();
    expect(workspaceEnvs).toEqual({});
    expect(byEnv).toEqual({});
    expect(navigate).not.toHaveBeenCalled();
  });

  it("shows the three CLI steps on the Build-locally tab", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("tab", { name: /Build locally/ }));

    expect(
      screen.getByText(
        "fai env init customer-support-line --template env-voice-support",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("fai sim run --env customer-support-line --pack core"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("fai env deploy customer-support-line"),
    ).toBeInTheDocument();
  });

  it("renders the surface header when showName is set", () => {
    renderPanel({ showName: true });
    expect(screen.getByText("Voice")).toBeInTheDocument();
    expect(screen.getByText("Customer Support Line")).toBeInTheDocument();
    expect(
      screen.getByText("Inbound phone support for an online storefront"),
    ).toBeInTheDocument();
  });
});
