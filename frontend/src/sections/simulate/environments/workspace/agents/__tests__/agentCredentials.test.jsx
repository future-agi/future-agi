import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { toVersions } from "src/api/simulate-environments/versions";
import {
  agentItem,
  mutations,
  resetMutations,
  useFakeMutation,
} from "./agentVersionsTestUtils";

vi.mock("src/api/simulate-environments/versions", async () => {
  const actual = await vi.importActual(
    "src/api/simulate-environments/versions",
  );
  return {
    ...actual,
    useChangeCredentials: vi.fn(() => useFakeMutation("change")),
  };
});
vi.mock("src/api/simulate-environments/environments", () => ({
  useUploadSecretFile: vi.fn(() => useFakeMutation("upload")),
}));

const { default: AgentCredentials } = await import("../AgentCredentials");

const SECRET = "sk-live-0123456789";
const DEFAULT = agentItem({
  id: "a4",
  label: "v4",
  is_default: true,
  preflight_status: "passed",
  credential_names: ["API_KEY", "REGION"],
  required_variable_names: ["API_KEY", "REGION", "WEBHOOK_TOKEN"],
});
const FILE_REF = {
  manager: "platform-vault",
  key: "harness-google-adc-1",
  version: "1",
  purpose: "target_provider",
};

const agents = (items) => toVersions({ agent: items }).agent;
const renderCredentials = (items = [DEFAULT]) => {
  const list = agents(items);
  const props = {
    envId: "env-1",
    defaultVersion: list.find((one) => one.isDefault) || null,
    versions: list,
  };
  const view = render(<AgentCredentials {...props} />);
  return {
    ...view,
    update: (next) => {
      const nextList = agents(next);
      view.rerender(
        <AgentCredentials
          {...props}
          defaultVersion={nextList.find((one) => one.isDefault) || null}
          versions={nextList}
        />,
      );
    },
  };
};
const openForm = (user) =>
  user.click(screen.getByRole("button", { name: "Change values" }));
const valueInput = (n) => screen.getByLabelText(`Value ${n}`);
const nameInput = (n) => screen.getByLabelText(`Name ${n}`);
const save = (user) =>
  user.click(screen.getByRole("button", { name: "Save values" }));
const passwordInputs = () =>
  Array.from(document.querySelectorAll('input[type="password"]'));
// No field holds the value and no rendered text shows it.
const expectNoValue = (value) => {
  expect(screen.queryByDisplayValue(value)).toBeNull();
  expect(screen.queryByText(value, { exact: false })).toBeNull();
};

beforeEach(() => {
  resetMutations();
  mutations.change.data = { agent_version: { id: "a8", label: "v8" } };
  mutations.upload.data = {
    secret_ref: FILE_REF,
    environment_name: "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    name: "service-account.json",
    size: 120,
  };
});

describe("AgentCredentials", () => {
  it("lists the default version's credential names and marks required ones not set", () => {
    renderCredentials();
    const names = screen
      .getAllByTestId("credential-name")
      .map((one) => one.textContent);
    expect(names).toEqual(["API_KEY", "REGION", "WEBHOOK_TOKEN"]);
    expect(screen.getByText("required, not set")).toBeInTheDocument();
    expect(
      screen.getByText("Variables the default version v4 runs with."),
    ).toBeInTheDocument();
  });

  it("marks nothing as missing when the source's variables aren't known", () => {
    renderCredentials([{ ...DEFAULT, required_variable_names: null }]);
    expect(screen.queryByText("required, not set")).toBeNull();
  });

  it("says when the default version has no credentials", () => {
    renderCredentials([
      { ...DEFAULT, credential_names: [], required_variable_names: [] },
    ]);
    expect(
      screen.getByText("No credentials are stored for the default version."),
    ).toBeInTheDocument();
  });

  it("prefills the names with empty password fields", async () => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);

    expect(nameInput(1)).toHaveValue("API_KEY");
    expect(nameInput(2)).toHaveValue("REGION");
    expect(nameInput(3)).toHaveValue("WEBHOOK_TOKEN");
    expect(passwordInputs()).toHaveLength(3);
    for (const input of passwordInputs()) {
      expect(input).toHaveValue("");
      expect(input).toHaveAttribute("autocomplete", "new-password");
    }
  });

  it("sends only the values filled in, once, then clears them and never shows them", async () => {
    const user = userEvent.setup();
    const { update } = renderCredentials();
    await openForm(user);
    await user.type(valueInput(1), SECRET);
    await save(user);

    expect(mutations.change.calls).toEqual([
      { values: { API_KEY: SECRET }, files: undefined },
    ]);
    // The form closes and nothing holds the value any more.
    expect(passwordInputs()).toHaveLength(0);
    expectNoValue(SECRET);

    // The new version is followed while it is checked…
    update([
      agentItem({
        id: "a8",
        label: "v8",
        status: "draft",
        preflight_status: "pending",
      }),
      DEFAULT,
    ]);
    const saved = screen.getByTestId("credentials-saved");
    expect(saved).toHaveTextContent("Saved as v8.");
    expect(saved).toHaveTextContent("Checking…");

    // …and once it passed, why it isn't the default.
    update([
      agentItem({
        id: "a8",
        label: "v8",
        preflight_status: "passed",
        activation_outcome: "environment_busy",
      }),
      DEFAULT,
    ]);
    expect(screen.getByTestId("credentials-saved")).toHaveTextContent(
      "Check passed",
    );
    expect(screen.getByTestId("credentials-saved")).toHaveTextContent(
      "Not made default: the environment was busy",
    );

    // Opening the form again starts with empty values.
    await openForm(user);
    for (const input of passwordInputs()) expect(input).toHaveValue("");
    expectNoValue(SECRET);
  });

  it("shows what a failed check found missing for the saved version", async () => {
    const user = userEvent.setup();
    const { update } = renderCredentials();
    await openForm(user);
    await user.type(valueInput(1), SECRET);
    await save(user);
    update([
      agentItem({
        id: "a8",
        label: "v8",
        status: "draft",
        preflight_status: "failed",
        preflight_report: {
          checks: [],
          missing: ["WEBHOOK_TOKEN"],
          invalid: [],
          error: null,
        },
      }),
      DEFAULT,
    ]);
    const saved = screen.getByTestId("credentials-saved");
    expect(saved).toHaveTextContent("Check failed");
    expect(saved).toHaveTextContent("Missing: WEBHOOK_TOKEN");
  });

  it("adds a variable and leaves a removed one out", async () => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);

    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(nameInput(4), "NEW_TOKEN");
    await user.type(valueInput(4), "new-value");
    await user.type(valueInput(2), "eu-west-1");
    await user.click(
      screen.getByRole("button", { name: "Don't change REGION" }),
    );
    await save(user);

    expect(mutations.change.calls).toEqual([
      { values: { NEW_TOKEN: "new-value" }, files: undefined },
    ]);
  });

  it.each([
    ["1BAD", "Use letters, digits and underscores, not starting with a digit."],
    [
      "BAD-NAME",
      "Use letters, digits and underscores, not starting with a digit.",
    ],
  ])("refuses the name %s before sending", async (name, message) => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(nameInput(4), name);
    await user.type(valueInput(4), "value");
    await save(user);

    expect(screen.getAllByText(message).length).toBeGreaterThan(0);
    expect(mutations.change.calls).toEqual([]);
  });

  it("refuses two values for one name", async () => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(nameInput(4), "API_KEY");
    await user.type(valueInput(4), "one");
    await user.type(valueInput(1), "two");
    await save(user);

    expect(screen.getAllByText("This name is used twice.")).toHaveLength(2);
    expect(mutations.change.calls).toEqual([]);
  });

  it("leaves the names of rows it doesn't send alone", async () => {
    const user = userEvent.setup();
    renderCredentials([{ ...DEFAULT, credential_names: ["legacy-alias"] }]);
    await openForm(user);
    await user.type(valueInput(2), "token");
    await save(user);

    expect(mutations.change.calls).toEqual([
      { values: { API_KEY: "token" }, files: undefined },
    ]);
  });

  it("refuses a value without a name", async () => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(valueInput(4), "value");
    await save(user);
    expect(
      screen.getByText(
        "Use letters, digits and underscores, not starting with a digit.",
      ),
    ).toBeInTheDocument();
    expect(mutations.change.calls).toEqual([]);
  });

  it("asks for a value or a file before saving", async () => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);
    await save(user);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Fill in at least one value or upload a file.",
    );
    expect(mutations.change.calls).toEqual([]);
  });

  it("uploads a service-account file and sends it by its ref", async () => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);
    const file = new File(['{"type":"service_account"}'], "sa.json", {
      type: "application/json",
    });
    await user.upload(screen.getByTestId("credential-file-input"), file);

    expect(mutations.upload.calls).toEqual([{ file }]);
    expect(screen.getByText("service-account.json")).toBeInTheDocument();
    await save(user);
    expect(mutations.change.calls).toEqual([{ values: {}, files: [FILE_REF] }]);
  });

  it("keeps only the uploaded file's ref and name, and drops the upload", async () => {
    const user = userEvent.setup();
    const { useUploadSecretFile } = await import(
      "src/api/simulate-environments/environments"
    );
    renderCredentials();
    await openForm(user);
    const file = new File(['{"private_key":"pk"}'], "sa.json", {
      type: "application/json",
    });
    mutations.upload.resets = 0;
    await user.upload(screen.getByTestId("credential-file-input"), file);

    expect(useUploadSecretFile).toHaveBeenCalledWith({ gcTime: 0 });
    // Reset right after the upload, so the mutation no longer holds the File.
    expect(mutations.upload.resets).toBe(1);
    expect(screen.getByText("service-account.json")).toBeInTheDocument();
    expect(screen.queryByText("pk", { exact: false })).toBeNull();
  });

  it("explains a failed upload", async () => {
    const user = userEvent.setup();
    mutations.upload.answer = {
      error: {
        statusCode: 400,
        detail:
          "Google credential file must be service-account JSON containing type, project_id, client_email, and private_key",
      },
    };
    renderCredentials();
    await openForm(user);
    await user.upload(
      screen.getByTestId("credential-file-input"),
      new File(["{}"], "bad.json", { type: "application/json" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Google credential file must be service-account JSON",
    );
  });

  it.each([
    [
      { statusCode: 413, error: "credential_blob_too_large" },
      "These values are too large to store. Remove some and try again.",
    ],
    [
      { statusCode: 422, error: "agent_versions_unavailable" },
      "This environment's agent can't take new credentials yet.",
    ],
    [
      { statusCode: 422, error: "secret_not_found" },
      "The uploaded file has expired. Upload it again.",
    ],
    [
      { statusCode: 422, error: "credential_missing", name: "REGION" },
      "A stored value for REGION is missing; enter it again.",
    ],
    [
      { statusCode: 422, error: "version_not_runnable" },
      "These variables can't be used yet: this environment would have to be prepared again with them, and that's turned off.",
    ],
    [
      {
        statusCode: 400,
        detail: "invalid or runner-reserved environment names: PATH",
      },
      "invalid or runner-reserved environment names: PATH",
    ],
  ])(
    "explains a refused save and clears the values (%#)",
    async (error, text) => {
      const user = userEvent.setup();
      mutations.change.answer = { error };
      renderCredentials();
      await openForm(user);
      await user.type(valueInput(1), SECRET);
      await save(user);

      expect(screen.getByRole("alert")).toHaveTextContent(text);
      // The form stays open with the names; the values are gone.
      expect(nameInput(1)).toHaveValue("API_KEY");
      for (const input of passwordInputs()) expect(input).toHaveValue("");
      expectNoValue(SECRET);
    },
  );

  it("closes the form on cancel without sending", async () => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);
    await user.type(valueInput(1), SECRET);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(passwordInputs()).toHaveLength(0);
    expect(mutations.change.calls).toEqual([]);
  });

  it("keeps Save busy while the values are being saved", async () => {
    const user = userEvent.setup();
    mutations.change.answer = "pending";
    renderCredentials();
    await openForm(user);
    await user.type(valueInput(1), SECRET);
    await save(user);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(
      within(screen.getByTestId("agent-credentials")).getByRole("button", {
        name: "Cancel",
      }),
    ).toBeDisabled();
  });

  it("blocks a change while a credentials check is still running", async () => {
    const user = userEvent.setup();
    renderCredentials([
      agentItem({
        id: "a8",
        label: "v8",
        status: "draft",
        preflight_status: "pending",
        requested_here: true,
        created_at: new Date().toISOString(),
      }),
      DEFAULT,
    ]);
    const change = screen.getByRole("button", { name: "Change values" });
    expect(change).toBeDisabled();
    await user.hover(change.parentElement);
    expect(
      await screen.findByText("A credentials check is still running"),
    ).toBeInTheDocument();
    expect(passwordInputs()).toHaveLength(0);
  });

  it("isn't held back by a stuck check", () => {
    renderCredentials([
      agentItem({
        id: "a8",
        label: "v8",
        status: "draft",
        preflight_status: "pending",
        requested_here: true,
        created_at: new Date(Date.now() - 16 * 60 * 1000).toISOString(),
      }),
      DEFAULT,
    ]);
    expect(screen.getByRole("button", { name: "Change values" })).toBeEnabled();
  });

  it("isn't held back by another environment's check", () => {
    renderCredentials([
      agentItem({
        id: "a8",
        label: "v8",
        status: "draft",
        preflight_status: "pending",
      }),
      DEFAULT,
    ]);
    expect(screen.getByRole("button", { name: "Change values" })).toBeEnabled();
  });

  it("warns that adding a variable may not be possible yet", async () => {
    const user = userEvent.setup();
    renderCredentials();
    await openForm(user);
    await user.type(valueInput(1), SECRET);
    expect(screen.queryByTestId("credentials-new-name-hint")).toBeNull();
    await user.type(valueInput(3), SECRET);
    expect(screen.getByTestId("credentials-new-name-hint")).toHaveTextContent(
      "Adding variables may not be possible yet",
    );
  });

  it("offers no form without a default version", () => {
    renderCredentials([]);
    expect(
      screen.getByText("There's no default agent version yet."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change values" })).toBeNull();
  });
});
