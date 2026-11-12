import React from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import { deploymentKeys } from "../api/queries";
import { fakeApi, renderDeployments } from "./helpers";
import { failRefetch, refetchErrors } from "./refetchFailure";
import { session, workspace } from "./session-mock";

const auth = { role: "Owner" };
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => auth,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: DeploymentSecretsPage } = await import(
  "../DeploymentSecretsPage"
);

const CANARY = "sk-vault-canary-3f9c2a7e";
const ROUTE = "/dashboard/deployments/secrets";
const at = (minutesAgo) =>
  new Date(Date.now() - minutesAgo * 60_000).toISOString();

function credential(overrides = {}) {
  return {
    credentialId: "cred_11111111-1111-4111-8111-111111111111",
    name: "OPENAI_API_KEY",
    kind: "api_key",
    status: "active",
    version: 1,
    createdAt: at(30),
    updatedAt: at(30),
    bindingCount: 0,
    ...overrides,
  };
}

function render(api) {
  return renderDeployments(<DeploymentSecretsPage />, { api, route: ROUTE });
}

/** A vault that behaves like EE's: every read returns what the writes stored. */
function vaultFake(initial = []) {
  const store = [...initial];
  return {
    store,
    credentials: vi.fn(async () => store.map((record) => ({ ...record }))),
    createCredential: vi.fn(async ({ name, kind }) => {
      const record = credential({
        credentialId: `cred_${store.length + 1}`,
        name,
        kind,
      });
      store.push(record);
      return record;
    }),
  };
}

const lostAnswer = () =>
  new OmegaError({
    state: "unavailable",
    status: 503,
    message: "Omega isn't responding right now.",
  });

async function fillAdd(name, value) {
  await userEvent.click(await screen.findByTestId("add-secret"));
  await userEvent.type(screen.getByTestId("secret-name"), name);
  await userEvent.type(screen.getByTestId("secret-value"), value);
}

/** Everything a person or the app could see or keep: the DOM and the whole query cache. */
function expectNoValueAnywhere(queryClient, value) {
  expect(document.body.innerHTML).not.toContain(value);
  const cached = JSON.stringify(
    queryClient
      .getQueryCache()
      .getAll()
      .map((q) => q.state.data),
  );
  expect(cached).not.toContain(value);
  const mutations = queryClient.getMutationCache().getAll();
  expect(
    JSON.stringify(mutations.map((m) => m.state.variables ?? null)),
  ).not.toContain(value);
}

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
  auth.role = "Owner";
  workspace.currentWorkspaceId = "ws-a";
});

describe("Secrets page", () => {
  it("lists names, kinds, versions and use, never a value", async () => {
    const api = fakeApi({
      credentials: vi.fn(async () => [
        credential({ version: 3, bindingCount: 2, lastUsedAt: at(5) }),
        credential({
          credentialId: "cred_2",
          name: "DB_PASSWORD",
          kind: "generic",
        }),
      ]),
    });
    render(api);
    const rows = await screen.findAllByTestId("secret-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("OPENAI_API_KEY");
    expect(rows[0]).toHaveTextContent("API key");
    expect(rows[0]).toHaveTextContent("Version 3");
    expect(rows[0]).toHaveTextContent("last read 5 min ago");
    expect(rows[0]).toHaveTextContent("Used in 2 places");
    expect(rows[1]).toHaveTextContent("Other secret");
    expect(rows[1]).toHaveTextContent("Not used yet");
    expect(screen.getByTestId("add-secret")).toBeInTheDocument();
  });

  it("adds a secret once, keyed, then forgets the value", async () => {
    const vault = vaultFake();
    const api = fakeApi({
      credentials: vault.credentials,
      createCredential: vault.createCredential,
    });
    const { queryClient } = render(api);
    await userEvent.click(await screen.findByTestId("add-secret"));
    await userEvent.type(screen.getByTestId("secret-name"), "openai_api_key");
    expect(screen.getByTestId("secret-name")).toHaveValue("OPENAI_API_KEY");
    const value = screen.getByTestId("secret-value");
    expect(value).toHaveAttribute("type", "password");
    expect(value).toHaveAttribute("autocomplete", "new-password");
    await userEvent.type(value, CANARY);
    await userEvent.dblClick(screen.getByTestId("save-secret"));

    await waitFor(() => expect(api.createCredential).toHaveBeenCalledTimes(1));
    const [input, { idempotencyKey }] = api.createCredential.mock.calls[0];
    expect(input).toEqual({
      name: "OPENAI_API_KEY",
      kind: "api_key",
      secret: CANARY,
    });
    expect(idempotencyKey).toMatch(/^fa-dep-secret-add-/);
    // The vault was read again just before the add (another tab may have added the name).
    expect(api.credentials.mock.invocationCallOrder[1]).toBeLessThan(
      api.createCredential.mock.invocationCallOrder[0],
    );
    expect(await screen.findByText("OPENAI_API_KEY saved")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByTestId("add-secret-form")).not.toBeInTheDocument(),
    );
    expect(await screen.findAllByTestId("secret-row")).toHaveLength(1);
    expectNoValueAnywhere(queryClient, CANARY);
  });

  it("checks the form before sending: names a deployment can inject, unique, a value", async () => {
    const api = fakeApi({
      credentials: vi.fn(async () => [credential()]),
      createCredential: vi.fn(),
    });
    render(api);
    await userEvent.click(await screen.findByTestId("add-secret"));
    await userEvent.click(screen.getByTestId("save-secret"));
    expect(screen.getByText("Name the secret.")).toBeInTheDocument();
    expect(screen.getByText("Enter the value.")).toBeInTheDocument();
    await userEvent.type(screen.getByTestId("secret-name"), "OPENAI_API_KEY");
    expect(screen.getByText(/already in the vault/)).toBeInTheDocument();
    await userEvent.clear(screen.getByTestId("secret-name"));
    await userEvent.type(screen.getByTestId("secret-name"), "MY-KEY");
    expect(screen.getByText(/A-Z, 0-9 and _/)).toBeInTheDocument();
    expect(api.createCredential).not.toHaveBeenCalled();
  });

  it("refuses a name another tab or admin added since the list was read, before sending, and shows the fresh list", async () => {
    const elsewhere = credential({ credentialId: "cred_tab2" });
    const api = fakeApi({
      // The page read an empty vault; by the time Save is pressed, tab 2 has added the name.
      credentials: vi
        .fn()
        .mockResolvedValueOnce([])
        .mockResolvedValue([elsewhere]),
      createCredential: vi.fn(),
    });
    const { queryClient } = render(api);
    expect(await screen.findByTestId("secrets-empty")).toBeInTheDocument();
    await fillAdd("OPENAI_API_KEY", CANARY);
    await userEvent.click(screen.getByTestId("save-secret"));
    expect(await screen.findByTestId("add-secret-error")).toHaveTextContent(
      "OPENAI_API_KEY is in the vault now (the list is refreshed). Replace its value instead. Nothing was saved.",
    );
    expect(api.createCredential).not.toHaveBeenCalled();
    expect(screen.getByText(/already in the vault/)).toBeInTheDocument();
    expect(await screen.findAllByTestId("secret-row")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByTestId("add-secret-form")).not.toBeInTheDocument(),
    );
    expectNoValueAnywhere(queryClient, CANARY);
  });

  it("shows a malformed preflight list in the add dialog without saving and rechecks on retry", async () => {
    const vault = vaultFake();
    const api = fakeApi({
      credentials: vi
        .fn()
        .mockResolvedValueOnce([])
        .mockRejectedValueOnce(
          new OmegaError({ state: "error", code: "malformed_response" }),
        )
        .mockImplementation(vault.credentials),
      createCredential: vault.createCredential,
    });
    render(api);
    await fillAdd("OPENAI_API_KEY", CANARY);
    await userEvent.click(screen.getByTestId("save-secret"));
    expect(await screen.findByTestId("add-secret-error")).toHaveTextContent(
      "Omega could not complete the request.",
    );
    expect(api.createCredential).not.toHaveBeenCalled();
    expect(screen.getByTestId("secret-name")).toHaveValue("OPENAI_API_KEY");
    await userEvent.click(screen.getByTestId("save-secret"));
    expect(await screen.findByText("OPENAI_API_KEY saved")).toBeInTheDocument();
    expect(api.createCredential).toHaveBeenCalledTimes(1);
    // Initial list, failed preflight, retried preflight, then refresh after saving.
    expect(api.credentials).toHaveBeenCalledTimes(4);
  });

  it("adds anyway when that last read fails: only EE can close the race", async () => {
    const api = fakeApi({
      credentials: vi
        .fn()
        .mockResolvedValueOnce([])
        .mockRejectedValueOnce(lostAnswer())
        .mockResolvedValue([credential()]),
      createCredential: vi.fn(async ({ name, kind }) =>
        credential({ name, kind }),
      ),
    });
    render(api);
    await fillAdd("OPENAI_API_KEY", CANARY);
    await userEvent.click(screen.getByTestId("save-secret"));
    expect(await screen.findByText("OPENAI_API_KEY saved")).toBeInTheDocument();
    expect(api.createCredential).toHaveBeenCalledTimes(1);
  });

  it("after a lost answer, the same entries keep their key and edited entries get a new one (no 409)", async () => {
    let answering = false;
    const vault = vaultFake();
    const api = fakeApi({
      credentials: vault.credentials,
      createCredential: vi.fn(async (input, keys) => {
        if (!answering) throw lostAnswer();
        return vault.createCredential(input, keys);
      }),
    });
    render(api);
    await fillAdd("A_KEY", "first-value");
    await userEvent.click(screen.getByTestId("save-secret"));
    // One automatic re-send (same key), then the dialog says what is safe.
    expect(
      await screen.findByTestId("add-secret-error", {}, { timeout: 5000 }),
    ).toHaveTextContent("Save again: the vault adds the same entry only once.");
    await userEvent.click(screen.getByTestId("save-secret"));
    await waitFor(() => expect(api.createCredential).toHaveBeenCalledTimes(4), {
      timeout: 5000,
    });
    const keys = () =>
      api.createCredential.mock.calls.map(([, k]) => k.idempotencyKey);
    expect(new Set(keys()).size).toBe(1);

    // EE keys a receipt to the exact request: an edited value must not reuse it.
    answering = true;
    await userEvent.type(screen.getByTestId("secret-value"), "-edited");
    await userEvent.click(screen.getByTestId("save-secret"));
    expect(await screen.findByText("A_KEY saved")).toBeInTheDocument();
    const [input, { idempotencyKey }] = api.createCredential.mock.calls[4];
    expect(input.secret).toBe("first-value-edited");
    expect(idempotencyKey).not.toBe(keys()[0]);
    expect(vault.store).toHaveLength(1);
  });

  it("shows EE's refusal and says nothing was saved, keeping the form", async () => {
    const api = fakeApi({
      credentials: vi.fn(async () => []),
      createCredential: vi.fn(async () => {
        throw new OmegaError({
          state: "error",
          status: 400,
          code: "validation",
          message: "Credential secret is invalid.",
        });
      }),
    });
    render(api);
    await userEvent.click(await screen.findByTestId("add-secret"));
    await userEvent.type(screen.getByTestId("secret-name"), "A_KEY");
    await userEvent.type(screen.getByTestId("secret-value"), "x");
    await userEvent.click(screen.getByTestId("save-secret"));
    expect(await screen.findByTestId("add-secret-error")).toHaveTextContent(
      "Credential secret is invalid. Nothing was saved.",
    );
    expect(screen.getByTestId("secret-name")).toHaveValue("A_KEY");
  });

  it("replaces a value in one keyed step; a refused replace keeps the current value", async () => {
    const api = fakeApi({
      credentials: vi.fn(async () => [credential({ version: 1 })]),
      rotateCredential: vi
        .fn()
        .mockRejectedValueOnce(
          new OmegaError({
            state: "error",
            status: 400,
            code: "validation",
            message: "Credential secret is invalid.",
          }),
        )
        .mockResolvedValue(credential({ version: 2 })),
    });
    const { queryClient } = render(api);
    await userEvent.click(await screen.findByTestId("replace-secret"));
    const dialog = screen.getByTestId("replace-secret-form");
    expect(dialog).toHaveTextContent(
      "The current value stays in force until the new one is saved.",
    );
    expect(dialog).toHaveTextContent(
      "Revisions already running keep the value they started with",
    );
    await userEvent.click(within(dialog).getByTestId("confirm-replace-secret"));
    expect(within(dialog).getByText("Enter the value.")).toBeInTheDocument();
    expect(api.rotateCredential).not.toHaveBeenCalled();

    await userEvent.type(
      within(dialog).getByTestId("replace-secret-value"),
      CANARY,
    );
    await userEvent.click(within(dialog).getByTestId("confirm-replace-secret"));
    expect(await screen.findByTestId("replace-secret-error")).toHaveTextContent(
      "The current value is still in force.",
    );
    const firstKey = api.rotateCredential.mock.calls[0][2].idempotencyKey;
    await userEvent.click(within(dialog).getByTestId("confirm-replace-secret"));
    await waitFor(() => expect(api.rotateCredential).toHaveBeenCalledTimes(2));
    const [id, body, { idempotencyKey }] = api.rotateCredential.mock.calls[1];
    expect(id).toBe("cred_11111111-1111-4111-8111-111111111111");
    expect(body).toEqual({ secret: CANARY });
    // The same intent keeps its key, so a retry after a lost answer lands once.
    expect(idempotencyKey).toBe(firstKey);
    expect(
      await screen.findByText("OPENAI_API_KEY: new value saved"),
    ).toBeInTheDocument();
    expectNoValueAnywhere(queryClient, CANARY);
  });

  it("a replace whose answer was lost: saving the same value keeps the key, a new value gets a new one", async () => {
    let answering = false;
    const api = fakeApi({
      credentials: vi.fn(async () => [credential({ version: 1 })]),
      rotateCredential: vi.fn(async () => {
        if (!answering) throw lostAnswer();
        return credential({ version: 2 });
      }),
    });
    render(api);
    await userEvent.click(await screen.findByTestId("replace-secret"));
    const dialog = screen.getByTestId("replace-secret-form");
    await userEvent.type(
      within(dialog).getByTestId("replace-secret-value"),
      "value-1",
    );
    await userEvent.click(within(dialog).getByTestId("confirm-replace-secret"));
    expect(
      await screen.findByTestId("replace-secret-error", {}, { timeout: 5000 }),
    ).toHaveTextContent(
      "If it did not go through, the current value is still in force. Saving again is safe.",
    );
    const keys = () =>
      api.rotateCredential.mock.calls.map(([, , k]) => k.idempotencyKey);
    await userEvent.click(within(dialog).getByTestId("confirm-replace-secret"));
    await waitFor(() => expect(api.rotateCredential).toHaveBeenCalledTimes(4), {
      timeout: 5000,
    });
    expect(new Set(keys()).size).toBe(1);

    answering = true;
    await userEvent.type(
      within(dialog).getByTestId("replace-secret-value"),
      "-2",
    );
    await userEvent.click(within(dialog).getByTestId("confirm-replace-secret"));
    expect(
      await screen.findByText("OPENAI_API_KEY: new value saved"),
    ).toBeInTheDocument();
    const [, body, { idempotencyKey }] = api.rotateCredential.mock.calls[4];
    expect(body).toEqual({ secret: "value-1-2" });
    expect(idempotencyKey).not.toBe(keys()[0]);
  });

  it("deletes only after the name is typed, and says what still depends on it", async () => {
    const api = fakeApi({
      credentials: vi.fn(async () => [credential({ bindingCount: 1 })]),
      revokeCredential: vi.fn(async () => true),
    });
    render(api);
    await userEvent.click(await screen.findByTestId("delete-secret"));
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "It is used in 1 place.",
    );
    const confirm = screen.getByTestId("confirm-delete-secret");
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByTestId("delete-secret-confirm-name"),
      "OPENAI_API_",
    );
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByTestId("delete-secret-confirm-name"),
      "KEY",
    );
    await userEvent.click(confirm);
    await waitFor(() => expect(api.revokeCredential).toHaveBeenCalledTimes(1));
    expect(api.revokeCredential.mock.calls[0][0]).toBe(
      "cred_11111111-1111-4111-8111-111111111111",
    );
    expect(api.revokeCredential.mock.calls[0][1].idempotencyKey).toMatch(
      /^fa-dep-secret-revoke-/,
    );
    expect(
      await screen.findByText("OPENAI_API_KEY deleted"),
    ).toBeInTheDocument();
  });

  it("members see names only, with one line saying who can change them", async () => {
    auth.role = "workspace_admin";
    const api = fakeApi({ credentials: vi.fn(async () => [credential()]) });
    render(api);
    expect(await screen.findAllByTestId("secret-row")).toHaveLength(1);
    expect(screen.getByTestId("secrets-role-note")).toHaveTextContent(
      "Workspace owners and admins add, replace and delete secrets.",
    );
    for (const id of ["add-secret", "replace-secret", "delete-secret"]) {
      expect(screen.queryByTestId(id)).not.toBeInTheDocument();
    }
  });

  it("an empty vault offers Add to admins and says who can to members", async () => {
    const api = fakeApi({ credentials: vi.fn(async () => []) });
    const first = render(api);
    const empty = await screen.findByTestId("secrets-empty");
    expect(within(empty).getByTestId("add-secret")).toBeInTheDocument();
    first.unmount();

    auth.role = "Member";
    render(fakeApi({ credentials: vi.fn(async () => []) }));
    const memberEmpty = await screen.findByTestId("secrets-empty");
    expect(memberEmpty).toHaveTextContent(
      "Workspace owners and admins add secrets here.",
    );
    expect(
      within(memberEmpty).queryByTestId("add-secret"),
    ).not.toBeInTheDocument();
  });

  it("a role refusal from the gateway shows in the dialog, not as a blank page", async () => {
    const api = fakeApi({
      credentials: vi.fn(async () => []),
      createCredential: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 403,
          code: "omega_role_not_permitted",
          message: "Your role in this workspace doesn't allow this action.",
        });
      }),
    });
    render(api);
    await userEvent.click(await screen.findByTestId("add-secret"));
    await userEvent.type(screen.getByTestId("secret-name"), "A_KEY");
    await userEvent.type(screen.getByTestId("secret-value"), "v");
    await userEvent.click(screen.getByTestId("save-secret"));
    expect(await screen.findByTestId("add-secret-error")).toHaveTextContent(
      "doesn't allow this action. Nothing was saved.",
    );
  });

  it("shows a malformed vault list as an error, never No secrets yet", async () => {
    const api = fakeApi({
      credentials: vi.fn().mockRejectedValue(
        new OmegaError({
          state: "error",
          code: "malformed_response",
        }),
      ),
    });
    render(api);
    const error = await screen.findByTestId("deployment-error-state");
    expect(error).toHaveTextContent("Omega could not complete the request.");
    expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
    expect(screen.queryByText("No secrets yet")).not.toBeInTheDocument();
    expect(api.credentials).toHaveBeenCalledTimes(1);
    api.credentials.mockResolvedValue([]);
    await userEvent.click(
      within(error).getByRole("button", { name: "Try again" }),
    );
    expect(await screen.findByTestId("secrets-empty")).toBeInTheDocument();
  });

  it("does not offer Add secret to an admin when the first vault read is refused", async () => {
    auth.role = "Admin";
    render(
      fakeApi({
        credentials: vi.fn().mockRejectedValue(
          new OmegaError({
            state: "refused",
            status: 403,
            code: "role_forbidden",
          }),
        ),
      }),
    );
    expect(
      await screen.findByTestId("deployment-error-state"),
    ).toHaveTextContent("Only workspace owners and admins can do this");
    expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Add secret" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("add-secret-form")).not.toBeInTheDocument();
  });

  describe.each(["Admin", "Member"])("%s error-state actions", (role) => {
    it("does not offer Add secret when the first vault read fails", async () => {
      auth.role = role;
      render(
        fakeApi({
          credentials: vi.fn().mockRejectedValue(refetchErrors[0]),
        }),
      );
      expect(
        await screen.findByTestId("deployment-error-state"),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Add secret" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByTestId("add-secret-form")).not.toBeInTheDocument();
    });

    it.each(refetchErrors)(
      "offers Add secret only to admins after an empty vault's $code refetch failure",
      async (error) => {
        auth.role = role;
        const api = fakeApi();
        const { queryClient } = render(api);
        expect(await screen.findByTestId("secrets-empty")).toBeInTheDocument();
        await failRefetch(
          queryClient,
          deploymentKeys.credentials(workspace.currentWorkspaceId),
          api.credentials,
          error,
        );
        expect(
          await screen.findByTestId("deployment-error-state"),
        ).toHaveTextContent(error.message);
        expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
        if (role === "Admin") {
          await userEvent.click(
            screen.getByRole("button", { name: "Add secret" }),
          );
          expect(
            await screen.findByTestId("add-secret-form"),
          ).toBeInTheDocument();
        } else {
          expect(
            screen.queryByRole("button", { name: "Add secret" }),
          ).not.toBeInTheDocument();
          expect(
            screen.queryByTestId("add-secret-form"),
          ).not.toBeInTheDocument();
        }
      },
    );
  });

  it.each(refetchErrors)(
    "replaces a successful empty vault with the error after a $code refetch failure",
    async (error) => {
      const api = fakeApi();
      const { queryClient } = render(api);
      expect(await screen.findByTestId("secrets-empty")).toHaveTextContent(
        "No secrets yet",
      );
      await failRefetch(
        queryClient,
        deploymentKeys.credentials(workspace.currentWorkspaceId),
        api.credentials,
        error,
      );
      expect(
        await screen.findByTestId("deployment-error-state"),
      ).toHaveTextContent(error.message);
      expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
      expect(screen.queryByText("No secrets yet")).not.toBeInTheDocument();
    },
  );

  it.each(refetchErrors)(
    "keeps cached secret rows after a $code refetch failure",
    async (error) => {
      const api = fakeApi({
        credentials: vi.fn().mockResolvedValue([credential()]),
      });
      const { queryClient } = render(api);
      expect(await screen.findAllByTestId("secret-row")).toHaveLength(1);
      await failRefetch(
        queryClient,
        deploymentKeys.credentials(workspace.currentWorkspaceId),
        api.credentials,
        error,
      );
      await waitFor(() => {
        expect(screen.getByTestId("secret-row")).toHaveTextContent(
          "OPENAI_API_KEY",
        );
        expect(
          screen.queryByTestId("deployment-error-state"),
        ).not.toBeInTheDocument();
        expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
      });
    },
  );

  it("Omega down: a calm error with Try again, never a blank page", async () => {
    const api = fakeApi({
      credentials: vi.fn(async () => {
        throw new OmegaError({
          state: "unavailable",
          status: 503,
          message: "Omega isn't responding right now.",
        });
      }),
    });
    render(api);
    // The read is retried twice first (isTransient), as on every Deployments page.
    expect(
      await screen.findByTestId(
        "deployment-error-state",
        {},
        { timeout: 8000 },
      ),
    ).toHaveTextContent("Omega isn't responding");
    expect(api.credentials).toHaveBeenCalledTimes(3);
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });

  it("each workspace reads its own vault (cache keys carry the workspace)", async () => {
    const api = fakeApi({
      credentials: vi.fn(async () => [
        credential({
          name: workspace.currentWorkspaceId === "ws-a" ? "A_ONLY" : "B_ONLY",
        }),
      ]),
    });
    const first = render(api);
    expect(await screen.findByText("A_ONLY")).toBeInTheDocument();
    first.unmount();
    workspace.currentWorkspaceId = "ws-b";
    const { queryClient } = render(api);
    expect(await screen.findByText("B_ONLY")).toBeInTheDocument();
    expect(screen.queryByText("A_ONLY")).not.toBeInTheDocument();
    expect(
      queryClient.getQueryData(["omega-deployments", "ws-a", "credentials"]),
    ).toBeUndefined();
  });
});
