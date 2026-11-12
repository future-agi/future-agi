import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { seedFromTemplate } from "../../workspace/helpers/seedEnvState";

const { navigate, notify, get, post } = vi.hoisted(() => {
  const refuseRequest = () => {
    throw new Error("Template previews must not make network requests");
  };
  return {
    navigate: vi.fn(),
    notify: vi.fn(),
    get: vi.fn(refuseRequest),
    post: vi.fn(refuseRequest),
  };
});

vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual("react-router-dom")),
  useNavigate: () => navigate,
}));
vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {},
}));
vi.mock("notistack", () => ({ enqueueSnackbar: notify }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// Exercise the actual adoption hook: a mocked success callback would conceal
// the fabricated-id bug this regression is intended to catch.
const { default: TemplateBuildPanel } = await import("../TemplateBuildPanel");
const { useEnvironmentsStore, resetEnvironmentsStore } = await import(
  "../../store/useEnvironmentsStore"
);

const template = {
  id: "library-support",
  name: "Support template",
  surface: "chat",
  difficulty: "Starter",
  tools: [{ name: "lookup_order", desc: "Look up an order" }],
  rules: ["Refunds need approval"],
  seed: { tables: [{ name: "orders", rows: 3, note: "delayed" }] },
  evalPreset: ["task_success"],
};
const clients = [];

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  const panel = (value) => (
    <QueryClientProvider client={client}>
      <TemplateBuildPanel template={value} />
    </QueryClientProvider>
  );
  const view = render(panel(template));
  return { client, changeTemplate: (value) => view.rerender(panel(value)) };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetEnvironmentsStore();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  resetEnvironmentsStore();
});

describe("template draft build truth", () => {
  it("keeps the card enabled but never sends a fabricated id to navigation or workspace state", async () => {
    const user = userEvent.setup();
    const { client } = renderPanel();
    // Match both labels so RED reaches the original click, not a missing button.
    const action = screen.getByRole("button", {
      name: /^(Build environment|Open local draft)$/,
    });
    expect(action).toBeVisible();
    expect(action).toBeEnabled();
    await user.click(action);
    await waitFor(() => {
      expect(client.getMutationCache().getAll().at(-1)?.state.status).toBe(
        "success",
      );
    });

    expect(navigate).not.toHaveBeenCalled();
    expect(
      client.getMutationCache().getAll().at(-1).state.data,
    ).not.toHaveProperty("envId");
    expect(useEnvironmentsStore.getState().workspaceEnvs).toEqual({});
    expect(useEnvironmentsStore.getState().byEnv).toEqual({});
    expect(get).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
    expect(notify).not.toHaveBeenCalled();

    const preview = await screen.findByRole("region", {
      name: "Local draft preview",
    });
    const seeded = seedFromTemplate(template, "2026-01-01T00:00:00Z");
    expect(within(preview).getByText(seeded.agent.name)).toBeInTheDocument();
    const rows = within(preview).getAllByRole("listitem");
    expect(rows).toHaveLength(seeded.scenarios.length);
    seeded.scenarios.forEach((scenario, index) => {
      expect(rows[index]).toHaveTextContent(scenario.name);
      expect(rows[index]).toHaveTextContent(scenario.task);
    });
    expect(
      screen.getByRole("button", { name: "Open local draft" }),
    ).toBeEnabled();
  });

  it("explains the local-only action without sandbox, ready, or built success claims", () => {
    renderPanel();
    expect(
      screen.queryByText(/Spins up in an isolated sandbox/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/ready to run|already built|wired in/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Build in the cloud")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Opening a draft creates no cloud environment, sandbox or simulation run.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("tab", { name: "Preview here" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "Build locally" })).toBeEnabled();
  });

  it("can close and reopen the local draft without showing it for another template", async () => {
    const user = userEvent.setup();
    const { changeTemplate } = renderPanel();
    await user.click(screen.getByRole("button", { name: "Open local draft" }));
    expect(
      await screen.findByRole("region", { name: "Local draft preview" }),
    ).toBeVisible();
    expect(
      screen.getByText(/Leaving or refreshing discards it/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close draft" }));
    expect(
      screen.queryByRole("region", { name: "Local draft preview" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open local draft" }));
    expect(
      await screen.findByRole("region", { name: "Local draft preview" }),
    ).toBeVisible();
    changeTemplate({ ...template, id: "another-library-template" });
    expect(
      screen.queryByRole("region", { name: "Local draft preview" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open local draft" }),
    ).toBeEnabled();
    expect(navigate).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});
