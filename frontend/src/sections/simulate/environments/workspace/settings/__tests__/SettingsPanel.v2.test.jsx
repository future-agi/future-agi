import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SettingsPanel from "../SettingsPanel";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";
import { harnessDetailToEnvironment } from "src/api/simulate-environments/harnessDetail";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import { DELETE_DIALOG_COPY } from "../../../myEnvironments.constants";

const renameMutate = vi.fn();
const deleteMutate = vi.fn();
vi.mock("src/api/simulate-environments/environments", () => ({
  useRenameEnvironment: () => ({ mutate: renameMutate, isPending: false }),
  useDeleteEnvironment: () => ({ mutate: deleteMutate, isPending: false }),
}));

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (orig) => ({
  ...(await orig()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

const navigate = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useNavigate: () => navigate,
}));

const DETAIL = {
  id: "env-1",
  overview: { id: "env-1", name: "Ride booking", status: "completed" },
  contract: {
    tools: [{ name: "book_ride" }, { name: "quote_fare" }],
    end_conditions: {
      max_turns: 12,
      max_duration_seconds: 3600,
      clock: "real-time",
    },
  },
  settings: {
    runtime: {
      isolation: "dedicated_vm",
      cpu_units: 4,
      memory_mb: 8192,
      network_policy: "live",
      parallelism: 2,
    },
    security: { allowed_egress_domains: ["api.acme.com"] },
    artifacts: { level: "full", retention_days: 30 },
    source: { kind: "github", repository: "acme/agent", ref: "main" },
    // The real seed, already rounded the way JSON.parse rounds it.
    seed: JSON.parse("195790417783341077"),
    agent: {
      connector: "livekit",
      mode: "connect_only",
      config: { agent_name: "my-agent" },
      secrets: ["DEEPGRAM_API_KEY"],
      credential_files: [],
    },
  },
};

const mappedEnv = (detail = DETAIL) => harnessDetailToEnvironment(detail).env;

function renderPanel({
  detail = DETAIL,
  env = mappedEnv(detail),
  envState = { agent: { typeId: "livekit" }, runRepeats: 5 },
  patch = vi.fn(),
  backed = true,
  locked = false,
  onGo,
} = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  if (backed) client.setQueryData(harnessEnvironmentKey("env-1"), detail);
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <SettingsPanel
          env={env}
          envState={envState}
          patch={patch}
          backed={backed}
          locked={locked}
          onGo={onGo}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { patch };
}

const section = (name) => screen.getByRole("region", { name });

const pick = (combobox, option) => {
  fireEvent.mouseDown(combobox);
  fireEvent.click(screen.getByRole("option", { name: option }));
};

beforeEach(() => {
  renameMutate.mockClear();
  deleteMutate.mockClear();
  enqueueSnackbar.mockClear();
  navigate.mockClear();
});

describe("SettingsPanel v2", () => {
  it("Build details shows source repo and ref, connector, mode (moved from Overview)", () => {
    renderPanel();
    const card = section("Build details");
    expect(within(card).getByText("acme/agent@main")).toBeInTheDocument();
    expect(within(card).getByText("LiveKit")).toBeInTheDocument();
    expect(within(card).getByText("Connect only")).toBeInTheDocument();
    // The real runtime and artifacts the build recorded.
    expect(within(card).getByText("Dedicated VM")).toBeInTheDocument();
    expect(within(card).getByText("4 CPU · 8 GB memory")).toBeInTheDocument();
    expect(within(card).getByText("Full · kept 30 days")).toBeInTheDocument();
    // A seed past 2^53 is rounded by JSON, so it is never shown here.
    expect(within(card).queryByText(/1957904177833410/)).toBeNull();
  });

  it("Simulation defaults: repeats (shared), turns and time from end_conditions, at once from runtime if present, stuck after a soon chip; editing is flagged", () => {
    const { patch } = renderPanel();
    const card = section("Simulation defaults");
    expect(within(card).getByLabelText("UI only")).toBeInTheDocument();

    const repeats = within(card).getByRole("slider", {
      name: "Repeats per scenario",
    });
    expect(repeats).toHaveAttribute("aria-valuenow", "5");
    fireEvent.change(repeats, { target: { value: 6 } });
    expect(patch).toHaveBeenCalledWith({ runRepeats: 6 });

    const turns = within(card).getByRole("combobox", { name: "Turns" });
    expect(turns).toHaveTextContent("12 turns");
    expect(
      within(card).getByRole("combobox", { name: "Time per conversation" }),
    ).toHaveTextContent("60 minutes");
    expect(
      within(card).getByRole("combobox", { name: "Conversations at once" }),
    ).toHaveTextContent("2 at once");
    // Vel's wording under the picker; the build's own count only when it ran
    // fewer than it asked for.
    expect(card).toHaveTextContent("Every run takes up to 2 at once.");
    expect(card).not.toHaveTextContent("Parallel worlds");
    expect(within(card).getByText("Stuck after soon")).toBeInTheDocument();

    // Picking works, saving sends nothing and the real value comes back.
    pick(turns, "20 turns");
    expect(turns).toHaveTextContent("20 turns");
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expect(turns).toHaveTextContent("12 turns");
  });

  it("one conversation at a time doesn't claim a run finishes sooner", () => {
    const detail = {
      ...DETAIL,
      settings: {
        ...DETAIL.settings,
        runtime: { ...DETAIL.settings.runtime, parallelism: 1 },
        agent: { ...DETAIL.settings.agent, connector: "retell_chat" },
      },
    };
    renderPanel({ detail, envState: { agent: { typeId: "retell_chat" } } });
    const card = section("Simulation defaults");
    expect(card).toHaveTextContent(
      "Every run takes one conversation at a time.",
    );
    expect(card).not.toHaveTextContent("finishes sooner");
  });

  it("says when the build ran fewer conversations at once than it asked for", () => {
    renderPanel({
      env: {
        ...mappedEnv(DETAIL),
        parallelism: { requested: 4, admitted: 4, effective: 1 },
      },
    });
    expect(section("Simulation defaults")).toHaveTextContent(
      "Parallel worlds: 1 of 4.",
    );
  });

  it("lists simulated caller changes, as Vel's does, with the history still to come", () => {
    renderPanel();
    const card = section("Simulation defaults");
    expect(
      within(card).getByText("Simulated caller changes"),
    ).toBeInTheDocument();
    expect(within(card).getByText("History soon")).toBeInTheDocument();
  });

  it("Simulation defaults show - for a missing turn limit and runtime", () => {
    const detail = {
      ...DETAIL,
      contract: {
        ...DETAIL.contract,
        end_conditions: { max_turns: null, max_duration_seconds: 3600 },
      },
      settings: { ...DETAIL.settings, runtime: undefined },
    };
    renderPanel({ detail });
    const card = section("Simulation defaults");
    expect(
      within(card).getByRole("combobox", { name: "Turns" }),
    ).toHaveTextContent("-");
    expect(
      within(card).getByRole("combobox", { name: "Conversations at once" }),
    ).toHaveTextContent("-");
  });

  it("Egress lists settings.security allowed domains if present, else a soon chip; editing is flagged", () => {
    renderPanel();
    const card = section("Egress");
    expect(within(card).getByLabelText("UI only")).toBeInTheDocument();
    expect(within(card).getByText("api.acme.com")).toBeInTheDocument();

    fireEvent.change(within(card).getByLabelText("Add egress domains"), {
      target: { value: "turn.acme.com" },
    });
    fireEvent.click(within(card).getByRole("button", { name: "Allow" }));
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expect(within(card).queryByText("turn.acme.com")).toBeNull();

    fireEvent.click(
      within(card).getByRole("button", { name: "Remove api.acme.com" }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(2);
    expect(within(card).getByText("api.acme.com")).toBeInTheDocument();

    // No security block sent: the list is a soon chip, the add row stays.
    cleanup();
    renderPanel({
      detail: { ...DETAIL, settings: { ...DETAIL.settings, security: null } },
    });
    const bare = section("Egress");
    expect(within(bare).getByText("Allowed domains soon")).toBeInTheDocument();
    expect(
      within(bare).getByLabelText("Add egress domains"),
    ).toBeInTheDocument();
  });

  it("environment variables keep the real list; add and set reference are flagged", () => {
    renderPanel();
    const card = section("Environment variables");
    expect(within(card).getByLabelText("UI only")).toBeInTheDocument();
    expect(within(card).getByText("DEEPGRAM_API_KEY")).toBeInTheDocument();
    expect(within(card).getByText("••••••••••••")).toBeInTheDocument();
    expect(within(card).getByText("agent_name")).toBeInTheDocument();
    expect(within(card).getByText("my-agent")).toBeInTheDocument();

    fireEvent.click(
      within(card).getByRole("button", { name: "Set reference" }),
    );
    fireEvent.change(within(card).getByLabelText("Secret reference"), {
      target: { value: "vault://acme/sim/deepgram" },
    });
    fireEvent.click(
      within(card).getByRole("button", { name: "Save reference" }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expect(within(card).queryByText("vault://acme/sim/deepgram")).toBeNull();

    fireEvent.change(within(card).getByLabelText("New variable name"), {
      target: { value: "LOG_LEVEL" },
    });
    fireEvent.click(within(card).getByRole("switch", { name: "Secret" }));
    fireEvent.change(within(card).getByLabelText("New variable value"), {
      target: { value: "info" },
    });
    fireEvent.click(within(card).getByRole("button", { name: "Add" }));
    expect(enqueueSnackbar).toHaveBeenCalledTimes(2);
    expect(within(card).queryByText("LOG_LEVEL")).toBeNull();
  });

  it("Danger zone deletes with a confirm (real)", () => {
    renderPanel();
    const card = section("Danger zone");
    fireEvent.click(
      within(card).getByRole("button", { name: "Delete environment" }),
    );
    expect(deleteMutate).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(DELETE_DIALOG_COPY.title),
    ).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", { name: DELETE_DIALOG_COPY.confirm }),
    );
    expect(deleteMutate).toHaveBeenCalledTimes(1);
    expect(deleteMutate.mock.calls[0][0]).toBe("env-1");
    deleteMutate.mock.calls[0][1].onSuccess();
    expect(navigate).toHaveBeenCalledWith(
      expect.stringContaining("tab=my-environments"),
    );
  });

  it("Versions says versioning is off, or opens the Versions tab while it is on", () => {
    renderPanel();
    const off = section("Versions");
    expect(
      within(off).getByText("Versioning is switched off for this environment."),
    ).toBeInTheDocument();
    expect(within(off).queryByText("Versions soon")).toBeNull();
    expect(within(off).queryByRole("button", { name: "Open versions" })).toBeNull();
  });

  it("opens the Versions tab from the Versions card while versioning is on", () => {
    const onGo = vi.fn();
    renderPanel({
      env: { ...mappedEnv(), versioning: { enabled: true } },
      onGo,
    });
    fireEvent.click(
      within(section("Versions")).getByRole("button", { name: "Open versions" }),
    );
    expect(onGo).toHaveBeenCalledWith("versions");
  });

  it("simulated user's model, replay flag and scenarios from production are flagged or soon", () => {
    renderPanel();
    expect(
      within(section("Simulation defaults")).getByText("Model soon"),
    ).toBeInTheDocument();

    const replay = section("Replay flag");
    expect(within(replay).getByLabelText("UI only")).toBeInTheDocument();
    expect(within(replay).getByText("book_ride")).toBeInTheDocument();
    fireEvent.click(
      within(replay).getByRole("checkbox", {
        name: "book_ride has a side effect",
      }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });

    const production = section("Scenarios from production");
    expect(within(production).getByLabelText("UI only")).toBeInTheDocument();
    fireEvent.click(
      within(production).getByRole("button", { name: "Turn on for workspace" }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(2);
  });

  it("before the build, settings show disabled instead of a message", () => {
    renderPanel({
      backed: false,
      env: { id: "env-1", name: "Draft env" },
      envState: undefined,
    });
    expect(screen.queryByText(/Settings appear once/)).toBeNull();
    expect(
      screen.getByRole("textbox", { name: "Environment name" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("slider", { name: "Repeats per scenario" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Add egress domains")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Delete environment" }),
    ).toBeDisabled();
  });
});
