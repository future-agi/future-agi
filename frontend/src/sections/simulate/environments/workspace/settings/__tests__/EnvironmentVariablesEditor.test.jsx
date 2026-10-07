import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SettingsPanel from "../SettingsPanel";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";

const mutate = vi.fn();
vi.mock("src/api/simulate-environments/environments", () => ({
  useRenameEnvironment: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateEnvironmentConfiguration: () => ({ mutate, isPending: false }),
}));

const uploadHarnessSecretFile = vi.fn();
vi.mock("src/api/harness/harness", () => ({
  uploadHarnessSecretFile: (...args) => uploadHarnessSecretFile(...args),
}));

const detail = (status, overrides = {}) => ({
  id: "env-1",
  overview: { id: "env-1", name: "Clinic", status },
  settings: {
    agent: {
      connector: "vapi",
      config: {
        assistant_id: "asst_1",
        inbound: true,
        phone_number: "+14155550100",
        target_system_prompt: "Book rides for callers.",
        dynamic_variables: { tier: "gold" },
      },
      secret_refs: ["DEEPGRAM_API_KEY", "VAPI_API_KEY"],
      secrets: ["DEEPGRAM_API_KEY", "VAPI_API_KEY"],
      credential_files: [],
      ...overrides,
    },
  },
});

function renderPanel(status = "completed", overrides = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(
    harnessEnvironmentKey("env-1"),
    detail(status, overrides),
  );
  render(
    <QueryClientProvider client={client}>
      <SettingsPanel env={{ id: "env-1", name: "Clinic" }} backed />
    </QueryClientProvider>,
  );
}

const save = () =>
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
const sentBody = () => mutate.mock.calls[0][0].body;

const addRow = (key, value, { secret = true } = {}) => {
  const toggle = screen.getByLabelText("Secret");
  if (toggle.checked !== secret) fireEvent.click(toggle);
  fireEvent.change(screen.getByLabelText("New key name"), {
    target: { value: key },
  });
  fireEvent.change(screen.getByLabelText("New key value"), {
    target: { value },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
};

const setVariables = (text) =>
  fireEvent.change(screen.getByLabelText("Dynamic variables"), {
    target: { value: text },
  });

beforeEach(() => {
  mutate.mockReset();
  uploadHarnessSecretFile.mockReset();
});

describe("settings on a built environment", () => {
  it("has only two sections: environment variables and credential files", () => {
    renderPanel();
    expect(screen.getByText("Environment variables")).toBeInTheDocument();
    expect(screen.getByText("Credential files")).toBeInTheDocument();
    expect(screen.queryByText("Connection")).toBeNull();
    expect(screen.queryByText("Agent")).toBeNull();
    expect(
      screen.getAllByRole("button", { name: "Save changes" }),
    ).toHaveLength(1);
  });

  it("lists secrets masked and our own config values in one list", () => {
    renderPanel();
    expect(screen.getAllByText("••••••••••••")).toHaveLength(2);
    expect(screen.getByText("asst_1")).toBeInTheDocument();
    expect(screen.getByText("+14155550100")).toBeInTheDocument();
    expect(screen.getByText("Book rides for callers.")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Agent always speaks first"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Dynamic variables")).toHaveValue("tier=gold");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("replaces a secret with the pencil and saves only that key", () => {
    renderPanel();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace DEEPGRAM_API_KEY" }),
    );
    fireEvent.change(screen.getByLabelText("New value for DEEPGRAM_API_KEY"), {
      target: { value: "deepgram-new" },
    });
    save();

    expect(mutate.mock.calls[0][0]).toEqual({
      id: "env-1",
      body: { environment_values: { DEEPGRAM_API_KEY: "deepgram-new" } },
    });
  });

  it("adds a secret", () => {
    renderPanel();
    addRow("gemini_api_key", "g-1");

    expect(screen.getByText("GEMINI_API_KEY")).toBeInTheDocument();
    save();
    expect(sentBody()).toEqual({
      environment_values: { GEMINI_API_KEY: "g-1" },
    });
  });

  it("will not add a secret under a name that is already set", () => {
    renderPanel();
    addRow("DEEPGRAM_API_KEY", "x");

    expect(
      screen.getByText(
        "DEEPGRAM_API_KEY is already set. Use the pencil to replace it.",
      ),
    ).toBeInTheDocument();
  });

  it("edits a setting that needs no rebuild", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Edit phone_number" }));
    fireEvent.change(screen.getByLabelText("phone_number"), {
      target: { value: "+14155550199" },
    });
    fireEvent.click(screen.getByLabelText("Agent always speaks first"));
    save();

    expect(sentBody()).toEqual({
      config: { phone_number: "+14155550199", target_speaks_first: true },
    });
  });

  it.each(["assistant_id", "inbound", "target_system_prompt"])(
    "explains in a tooltip that changing %s needs a rebuild",
    async (key) => {
      renderPanel();
      const pencil = screen.getByRole("button", { name: `Edit ${key}` });
      expect(pencil).toBeDisabled();
      fireEvent.mouseOver(pencil.parentElement);

      expect(await screen.findByRole("tooltip")).toHaveTextContent(
        `Needs a rebuildChanging ${key} requires a rebuild of the environment, so it can't be edited here yet.`,
      );
      expect(screen.queryByLabelText(key)).toBeNull();
    },
  );

  it("adds a config value to the variables box when Secret is off", () => {
    renderPanel();
    addRow("region", "eu", { secret: false });

    expect(screen.getByLabelText("Dynamic variables")).toHaveValue(
      "tier=gold\nregion=eu",
    );
    save();
    expect(sentBody()).toEqual({
      config: { dynamic_variables: { tier: "gold", region: "eu" } },
    });
  });

  it("will not add a config value under a name already in the box", () => {
    renderPanel();
    addRow("tier", "silver", { secret: false });

    expect(
      screen.getByText("tier is already set. Edit it in the box below."),
    ).toBeInTheDocument();
  });

  it("saves user-added variables edited as text", () => {
    renderPanel();
    setVariables("region=eu\ncustomer_name=Alex");
    save();

    expect(sentBody()).toEqual({
      config: { dynamic_variables: { region: "eu", customer_name: "Alex" } },
    });
  });

  it("blocks saving while a variable line cannot be read", () => {
    renderPanel();
    setVariables("tier=gold\nnotavariable");

    expect(
      screen.getByText("Line 2 needs the form KEY=value."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("shows which key a provider rejected", () => {
    mutate.mockImplementation((_vars, { onError }) =>
      onError({
        detail: "A provider rejected a changed key. Nothing was saved.",
        checks: [
          {
            aliases: ["DEEPGRAM_API_KEY"],
            label: "Deepgram",
            status: "rejected",
            message: "Deepgram rejected DEEPGRAM_API_KEY (HTTP 401)",
          },
        ],
      }),
    );
    renderPanel();
    fireEvent.click(
      screen.getByRole("button", { name: "Replace DEEPGRAM_API_KEY" }),
    );
    fireEvent.change(screen.getByLabelText("New value for DEEPGRAM_API_KEY"), {
      target: { value: "wrong" },
    });
    save();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "A provider rejected a changed key. Nothing was saved.",
    );
    expect(screen.getByText("Rejected")).toBeInTheDocument();
  });

  it("uploads a credential file in its own section and saves it at once", async () => {
    const ref = { manager: "platform-vault", key: "harness-google-adc-1" };
    uploadHarnessSecretFile.mockResolvedValue({ secret_ref: ref });
    mutate.mockImplementation((_vars, { onSuccess, onSettled }) => {
      onSuccess({ checks: [] });
      onSettled();
    });
    renderPanel();
    const file = new File(["{}"], "sa.json", { type: "application/json" });
    fireEvent.change(screen.getByTestId("credential-file-input"), {
      target: { files: [file] },
    });

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toEqual({
      id: "env-1",
      body: { credential_files: { GOOGLE_APPLICATION_CREDENTIALS_JSON: ref } },
    });
  });
});

describe("settings on an environment that is not built", () => {
  it.each(["failed", "building", "running"])(
    "stays read-only while the environment is %s",
    (status) => {
      renderPanel(status);
      expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Replace DEEPGRAM_API_KEY" }),
      ).toBeNull();
      expect(screen.queryByLabelText("Dynamic variables")).toBeNull();
    },
  );
});
