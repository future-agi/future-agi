import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SettingsPanel from "../SettingsPanel";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";

// The rename mutation is stubbed so we can assert what it's called with without
// hitting the network.
const mutate = vi.fn();
vi.mock("src/api/simulate-environments/environments", () => ({
  useRenameEnvironment: () => ({ mutate, isPending: false }),
}));

// §6 detail example (settings.agent has all three env-var groups).
const DETAIL = {
  id: "env-1",
  overview: { id: "env-1", name: "Ride booking" },
  settings: {
    agent: {
      connector: "livekit",
      config: { agent_name: "my-agent", livekit_url: "wss://acme.livekit.cloud" },
      secret_refs: ["DEEPGRAM_API_KEY", "LIVEKIT_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS_JSON"],
      secrets: ["DEEPGRAM_API_KEY", "LIVEKIT_API_KEY"],
      credential_files: [{ environment_name: "GOOGLE_APPLICATION_CREDENTIALS_JSON" }],
    },
  },
};

const env = { id: "env-1", name: "job-derived-name" };

function renderPanel({ backed = true, locked = false, seedDetail = true } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (seedDetail) client.setQueryData(harnessEnvironmentKey("env-1"), DETAIL);
  render(
    <QueryClientProvider client={client}>
      <SettingsPanel env={env} backed={backed} locked={locked} />
    </QueryClientProvider>,
  );
}

beforeEach(() => mutate.mockClear());

describe("SettingsPanel — environment variables (§11, read-only)", () => {
  it("renders secrets + config in the flat env-vars list with a kind chip", () => {
    renderPanel();
    expect(screen.getByText("DEEPGRAM_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("agent_name")).toBeInTheDocument();
    expect(screen.getByText("my-agent")).toBeInTheDocument();

    // Env-vars chips: two secrets, two config. Credential files are NOT here.
    expect(screen.getAllByText("Secret")).toHaveLength(2);
    expect(screen.getAllByText("Config")).toHaveLength(2);
  });

  it("puts credential files in their own section, not the env-vars list", () => {
    renderPanel();
    expect(screen.getByText("Credential files")).toBeInTheDocument();
    expect(screen.getByText("GOOGLE_APPLICATION_CREDENTIALS_JSON")).toBeInTheDocument();
    // A file is not an env variable — one File chip, in its own section.
    expect(screen.getAllByText("File")).toHaveLength(1);
  });

  it("masks secret values and keeps the lists read-only", () => {
    renderPanel();
    // Two secrets are masked; the two config values show in clear.
    expect(screen.getAllByText("••••••••••••")).toHaveLength(2);
    // No reveal / add / delete / version affordances.
    expect(screen.queryByRole("button", { name: /^Add$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /New version/ })).toBeNull();
    expect(screen.queryByText("Run defaults")).toBeNull();
    expect(screen.queryByText("Build arguments")).toBeNull();
  });

  it("shows an empty note when no variables were recorded", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(harnessEnvironmentKey("env-1"), {
      id: "env-1",
      overview: { name: "Empty env" },
      settings: { agent: {} },
    });
    render(
      <QueryClientProvider client={client}>
        <SettingsPanel env={env} backed locked={false} />
      </QueryClientProvider>,
    );
    expect(screen.getByText(/No variables were recorded/)).toBeInTheDocument();
  });
});

describe("SettingsPanel — rename (§8)", () => {
  it("seeds the field from the §6 name and keeps Save disabled until it changes", () => {
    renderPanel();
    const input = screen.getByRole("textbox");
    expect(input).toHaveValue("Ride booking");

    const save = screen.getByRole("button", { name: /^Rename$/ });
    expect(save).toBeDisabled();

    fireEvent.change(input, { target: { value: "Ride booking — voice" } });
    expect(save).toBeEnabled();
  });

  it("rejects a blank name without calling the mutation", () => {
    renderPanel();
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /^Rename$/ }));
    expect(mutate).not.toHaveBeenCalled();
  });

  it("calls the rename mutation with a trimmed name on a valid save", () => {
    renderPanel();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "  New name  " } });
    fireEvent.click(screen.getByRole("button", { name: /^Rename$/ }));
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({ id: "env-1", name: "New name" });
  });
});

describe("SettingsPanel — non-backed", () => {
  it("shows neither the rename field nor env vars for a non-backed env", () => {
    renderPanel({ backed: false, seedDetail: false });
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByText("Secrets")).toBeNull();
    expect(screen.getByText(/Settings appear once this environment has been built/)).toBeInTheDocument();
  });
});
