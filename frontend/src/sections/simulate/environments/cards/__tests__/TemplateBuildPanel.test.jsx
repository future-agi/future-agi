import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { packStats } from "../../helpers/packStats";

const navigate = vi.fn();
const adoptMutate = vi.fn((id, opts) => opts?.onSuccess?.({ envId: "env-x" }));

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
  rules: ["Refunds above $200 need approval", "Never confirm identity by phone"],
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
    expect(screen.getByRole("tab", { name: /Build here/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Build locally/ })).toBeInTheDocument();
  });

  it("shows the three cloud bullets on the Build-here tab", () => {
    renderPanel();
    expect(
      screen.getByText(/seeded world, tools and hard rules/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/baseline agent is wired in/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Add your own agent version afterwards/i),
    ).toBeInTheDocument();
  });

  it("computes the stats card from the template", () => {
    renderPanel();
    const rows = TEMPLATE.seed.tables.reduce((a, t) => a + t.rows, 0);
    expect(
      screen.getByText(`${rows.toLocaleString()} seeded rows`),
    ).toBeInTheDocument();
    expect(screen.getByText("3 the world answers")).toBeInTheDocument();
    expect(screen.getByText("2 graded on every run")).toBeInTheDocument();
    expect(
      screen.getByText(`${packStats(TEMPLATE).scenarios} ready to run`),
    ).toBeInTheDocument();
    expect(screen.getByText("2 suggested")).toBeInTheDocument();
    expect(
      screen.getByText("Seeded baseline (swap in yours after)"),
    ).toBeInTheDocument();
  });

  it("shows the nothing-touches-production note", () => {
    renderPanel();
    expect(screen.getByText(/Nothing touches production/i)).toBeInTheDocument();
  });

  it("seeds the workspace and navigates to it on Build environment", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: /Build environment/ }));

    expect(adoptMutate).toHaveBeenCalledWith(
      "env-voice-support",
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );

    // The env record is registered and its per-env state seeded from the
    // template — locked until forked.
    const { workspaceEnvs, byEnv } = useEnvironmentsStore.getState();
    expect(workspaceEnvs["env-x"]).toMatchObject({
      id: "env-x",
      templateId: "env-voice-support",
    });
    expect(byEnv["env-x"].seededFromTemplate).toBe(true);
    expect(byEnv["env-x"].scenarios.length).toBeGreaterThan(0);

    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/env-x",
    );
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
    expect(screen.getByText("VOICE")).toBeInTheDocument();
    expect(screen.getByText("Customer Support Line")).toBeInTheDocument();
    expect(
      screen.getByText("Inbound phone support for an online storefront"),
    ).toBeInTheDocument();
  });
});
