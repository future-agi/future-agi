import React, { useState } from "react";
import PropTypes from "prop-types";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { onlineManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import { deploymentKeys } from "../api/queries";
import { fakeApi, renderDeployments } from "./helpers";
import { failRefetch, refetchErrors } from "./refetchFailure";
import { workspace } from "./session-mock";

const auth = { role: "Admin" };
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => auth,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

const { default: SecretsField } = await import("../new/SecretsField");

const CANARY = "sk-wizard-canary-81d2";
const record = (name, extra = {}) => ({
  credentialId: `cred_${name.toLowerCase()}`,
  name,
  kind: "api_key",
  status: "active",
  version: 1,
  createdAt: "2026-10-19T10:00:00.000Z",
  updatedAt: "2026-10-19T10:00:00.000Z",
  bindingCount: 0,
  ...extra,
});

/** The field as the wizard mounts it, with its picked names shown for assertions. */
function Harness({ initial = [] }) {
  const [selected, setSelected] = useState(initial);
  return (
    <>
      <SecretsField
        selected={selected}
        onChange={setSelected}
        renderField={(body) => (
          <section data-testid="secrets-field">{body}</section>
        )}
      />
      <output data-testid="picked">{selected.join(",")}</output>
    </>
  );
}

Harness.propTypes = { initial: PropTypes.arrayOf(PropTypes.string) };

beforeEach(() => {
  auth.role = "Admin";
  workspace.currentWorkspaceId = "ws-a";
});

afterEach(() => {
  cleanup();
  onlineManager.setOnline(true);
});

describe("wizard Secrets field", () => {
  it("adds a secret without leaving the wizard and ticks it", async () => {
    let vault = [record("EXISTING_KEY")];
    const api = fakeApi({
      credentials: vi.fn(async () => vault),
      createCredential: vi.fn(async ({ name, kind }) => {
        vault = [...vault, record(name, { kind })];
        return record(name, { kind });
      }),
    });
    const { queryClient } = renderDeployments(<Harness />, {
      api,
      route: "/dashboard/deployments/new",
    });
    await userEvent.click(await screen.findByTestId("wizard-add-secret"));
    await userEvent.type(
      screen.getByTestId("secret-name"),
      "SERVE_FIXTURE_SECRET",
    );
    await userEvent.type(screen.getByTestId("secret-value"), CANARY);
    await userEvent.click(screen.getByTestId("save-secret"));

    await waitFor(() =>
      expect(screen.getByTestId("picked")).toHaveTextContent(
        "SERVE_FIXTURE_SECRET",
      ),
    );
    expect(
      await screen.findByLabelText("Secret SERVE_FIXTURE_SECRET"),
    ).toBeChecked();
    expect(screen.getByLabelText("Secret EXISTING_KEY")).not.toBeChecked();
    expect(api.createCredential).toHaveBeenCalledWith(
      { name: "SERVE_FIXTURE_SECRET", kind: "api_key", secret: CANARY },
      { idempotencyKey: expect.stringMatching(/^fa-dep-secret-add-/) },
    );
    expect(document.body.innerHTML).not.toContain(CANARY);
    expect(
      JSON.stringify(
        queryClient
          .getQueryCache()
          .getAll()
          .map((q) => q.state.data),
      ),
    ).not.toContain(CANARY);
  });

  it("an empty vault: admins get Add a secret; a member, with nothing to pick or add, gets no field", async () => {
    const first = renderDeployments(<Harness />, {
      api: fakeApi(),
      route: "/dashboard/deployments/new",
    });
    expect(await screen.findByTestId("secrets-empty")).toHaveTextContent(
      "Add the API keys this project reads.",
    );
    expect(screen.getByTestId("wizard-add-secret")).toBeInTheDocument();
    first.unmount();

    auth.role = "workspace_member";
    const api = fakeApi();
    renderDeployments(<Harness />, {
      api,
      route: "/dashboard/deployments/new",
    });
    // While the vault is read the field holds a placeholder, then it is left out.
    await waitFor(() => expect(api.credentials).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId("secrets-field")).not.toBeInTheDocument(),
    );
    expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wizard-add-secret")).not.toBeInTheDocument();
  });

  it.each(["Admin", "Member"])(
    "preserves %s add-secret visibility when the first vault read is paused offline without cached data",
    (role) => {
      auth.role = role;
      onlineManager.setOnline(false);
      const api = fakeApi();
      const { queryClient } = renderDeployments(<Harness />, {
        api,
        route: "/dashboard/deployments/new",
      });
      expect(
        queryClient.getQueryState(
          deploymentKeys.credentials(workspace.currentWorkspaceId),
        ),
      ).toMatchObject({
        data: undefined,
        error: null,
        status: "pending",
        fetchStatus: "paused",
      });
      expect(api.credentials).not.toHaveBeenCalled();
      if (role === "Admin") {
        expect(screen.getByTestId("secret-picker")).toBeInTheDocument();
        expect(screen.queryByTestId("wizard-add-secret")).toBeInTheDocument();
      } else {
        expect(screen.queryByTestId("secrets-field")).not.toBeInTheDocument();
        expect(
          screen.queryByTestId("wizard-add-secret"),
        ).not.toBeInTheDocument();
      }
    },
  );

  it("keeps a name picked by an earlier revision even when the vault no longer has it", async () => {
    renderDeployments(<Harness initial={["GONE_KEY"]} />, {
      api: fakeApi({ credentials: vi.fn(async () => [record("A_KEY")]) }),
      route: "/dashboard/deployments/new",
    });
    const field = await screen.findByTestId("secret-picker");
    expect(within(field).getByLabelText("Secret GONE_KEY")).toBeChecked();
    expect(field).toHaveTextContent("Not in the vault now.");
    await userEvent.click(within(field).getByLabelText("Secret GONE_KEY"));
    expect(screen.getByTestId("picked")).toHaveTextContent("");
  });

  it("a name carried over from an earlier revision that the vault will not inject holds the deploy until unticked", async () => {
    renderDeployments(<Harness initial={["A_KEY", "OMEGA_API_KEY"]} />, {
      api: fakeApi({
        credentials: vi.fn(async () => [
          record("A_KEY"),
          record("OMEGA_API_KEY"),
        ]),
      }),
      route: "/dashboard/deployments/new",
    });
    const field = await screen.findByTestId("secret-picker");
    const reserved = within(field).getByLabelText("Secret OMEGA_API_KEY");
    expect(reserved).toBeChecked();
    expect(reserved).toBeEnabled();
    expect(field).toHaveTextContent(
      "Names starting OMEGA_ are kept for Omega; a deployment cannot receive them.",
    );
    expect(screen.getByTestId("secrets-blocked")).toHaveTextContent(
      "Untick OMEGA_API_KEY to continue: a deployment cannot receive it.",
    );
    await userEvent.click(reserved);
    expect(screen.getByTestId("picked")).toHaveTextContent("A_KEY");
    expect(screen.queryByTestId("secrets-blocked")).not.toBeInTheDocument();
    // Once unticked it cannot be ticked again.
    expect(within(field).getByLabelText("Secret OMEGA_API_KEY")).toBeDisabled();
  });

  it("when the vault cannot be read, the names already picked stay listed and can be unticked", async () => {
    renderDeployments(<Harness initial={["A_KEY", "PATH"]} />, {
      api: fakeApi({
        credentials: vi.fn(async () => {
          throw new OmegaError({
            state: "refused",
            status: 403,
            message: "Credential vault is unavailable.",
          });
        }),
      }),
      route: "/dashboard/deployments/new",
    });
    expect(
      await screen.findByTestId("secrets-unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("secrets-blocked")).toHaveTextContent(
      "Untick PATH to continue",
    );
    expect(
      screen.getByText(
        "PATH is a system variable a deployment cannot receive.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Secret PATH"));
    expect(screen.getByTestId("picked")).toHaveTextContent("A_KEY");
    expect(screen.queryByTestId("secrets-blocked")).not.toBeInTheDocument();
  });

  it.each(["Admin", "Member"])(
    "shows a malformed credentials error to %s rather than an empty or missing field",
    async (role) => {
      auth.role = role;
      renderDeployments(<Harness />, {
        api: fakeApi({
          credentials: vi.fn().mockRejectedValue(
            new OmegaError({
              state: "error",
              code: "malformed_response",
            }),
          ),
        }),
        route: "/dashboard/deployments/new",
      });
      expect(
        await screen.findByTestId("secrets-unavailable"),
      ).toHaveTextContent("Omega could not complete the request.");
      expect(screen.getByTestId("secrets-field")).toBeInTheDocument();
      expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
      expect(screen.queryByTestId("wizard-add-secret")).not.toBeInTheDocument();
      expect(screen.queryByTestId("add-secret-form")).not.toBeInTheDocument();
    },
  );

  describe.each(["Admin", "Member"])("%s refetches", (role) => {
    it.each(refetchErrors)(
      "shows the error after an empty vault's $code refetch failure, never an empty or missing field",
      async (error) => {
        auth.role = role;
        const api = fakeApi();
        const { queryClient } = renderDeployments(<Harness />, {
          api,
          route: "/dashboard/deployments/new",
        });
        if (role === "Admin") {
          expect(await screen.findByTestId("secrets-empty")).toHaveTextContent(
            "No secrets in this workspace yet.",
          );
        } else {
          await waitFor(() => {
            expect(api.credentials).toHaveBeenCalledTimes(1);
            expect(
              screen.queryByTestId("secrets-field"),
            ).not.toBeInTheDocument();
          });
        }
        await failRefetch(
          queryClient,
          deploymentKeys.credentials(workspace.currentWorkspaceId),
          api.credentials,
          error,
        );
        expect(
          await screen.findByTestId("secrets-unavailable"),
        ).toHaveTextContent(error.message);
        expect(screen.getByTestId("secrets-field")).toBeInTheDocument();
        expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
        expect(
          screen.queryByText(/No secrets in this workspace yet/),
        ).not.toBeInTheDocument();
        if (role === "Admin") {
          expect(screen.queryByTestId("wizard-add-secret")).toBeInTheDocument();
          await userEvent.click(screen.getByTestId("wizard-add-secret"));
          expect(await screen.findByRole("dialog")).toContainElement(
            screen.getByTestId("add-secret-form"),
          );
        } else {
          expect(
            screen.queryByTestId("wizard-add-secret"),
          ).not.toBeInTheDocument();
          expect(
            screen.queryByTestId("add-secret-form"),
          ).not.toBeInTheDocument();
        }
      },
    );

    it.each(refetchErrors)(
      "shows the error after a revoked-only vault's $code refetch failure, never an empty or missing field",
      async (error) => {
        auth.role = role;
        const api = fakeApi({
          credentials: vi
            .fn()
            .mockResolvedValue([record("OLD", { status: "revoked" })]),
        });
        const { queryClient } = renderDeployments(<Harness />, {
          api,
          route: "/dashboard/deployments/new",
        });
        if (role === "Admin") {
          expect(await screen.findByTestId("secrets-empty")).toHaveTextContent(
            "No secrets in this workspace yet.",
          );
        } else {
          await waitFor(() => {
            expect(api.credentials).toHaveBeenCalledTimes(1);
            expect(
              screen.queryByTestId("secrets-field"),
            ).not.toBeInTheDocument();
          });
        }
        await failRefetch(
          queryClient,
          deploymentKeys.credentials(workspace.currentWorkspaceId),
          api.credentials,
          error,
        );
        expect(
          await screen.findByTestId("secrets-unavailable"),
        ).toHaveTextContent(error.message);
        expect(screen.getByTestId("secrets-field")).toBeInTheDocument();
        expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
        expect(
          screen.queryByText(/No secrets in this workspace yet/),
        ).not.toBeInTheDocument();
        if (role === "Admin") {
          expect(screen.queryByTestId("wizard-add-secret")).toBeInTheDocument();
          await userEvent.click(screen.getByTestId("wizard-add-secret"));
          expect(await screen.findByRole("dialog")).toContainElement(
            screen.getByTestId("add-secret-form"),
          );
        } else {
          expect(
            screen.queryByTestId("wizard-add-secret"),
          ).not.toBeInTheDocument();
          expect(
            screen.queryByTestId("add-secret-form"),
          ).not.toBeInTheDocument();
        }
        expect(screen.queryByLabelText("Secret OLD")).not.toBeInTheDocument();
      },
    );

    it.each(refetchErrors)(
      "shows the error and keeps selected OLD checked after a revoked-only vault's $code refetch failure",
      async (error) => {
        auth.role = role;
        const api = fakeApi({
          credentials: vi
            .fn()
            .mockResolvedValue([record("OLD", { status: "revoked" })]),
        });
        const { queryClient } = renderDeployments(
          <Harness initial={["OLD"]} />,
          {
            api,
            route: "/dashboard/deployments/new",
          },
        );
        expect(await screen.findByLabelText("Secret OLD")).toBeChecked();
        await failRefetch(
          queryClient,
          deploymentKeys.credentials(workspace.currentWorkspaceId),
          api.credentials,
          error,
        );
        expect(screen.queryByTestId("secrets-unavailable")).toBeInTheDocument();
        expect(screen.getByTestId("secrets-unavailable")).toHaveTextContent(
          error.message,
        );
        expect(screen.getByLabelText("Secret OLD")).toBeChecked();
        expect(screen.getByTestId("secrets-field")).toHaveTextContent("OLD");
        expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
        await userEvent.click(screen.getByLabelText("Secret OLD"));
        expect(screen.getByTestId("picked")).toBeEmptyDOMElement();
        expect(screen.getByTestId("secrets-unavailable")).toHaveTextContent(
          error.message,
        );
      },
    );

    it.each(refetchErrors)(
      "keeps cached secrets selectable after a $code refetch failure",
      async (error) => {
        auth.role = role;
        const api = fakeApi({
          credentials: vi.fn().mockResolvedValue([record("EXISTING_KEY")]),
        });
        const { queryClient } = renderDeployments(<Harness />, {
          api,
          route: "/dashboard/deployments/new",
        });
        expect(
          await screen.findByLabelText("Secret EXISTING_KEY"),
        ).not.toBeChecked();
        await failRefetch(
          queryClient,
          deploymentKeys.credentials(workspace.currentWorkspaceId),
          api.credentials,
          error,
        );
        await waitFor(() => {
          expect(screen.getByLabelText("Secret EXISTING_KEY")).toBeEnabled();
          expect(
            screen.queryByTestId("secrets-unavailable"),
          ).not.toBeInTheDocument();
          expect(screen.queryByTestId("secrets-empty")).not.toBeInTheDocument();
        });
        await userEvent.click(screen.getByLabelText("Secret EXISTING_KEY"));
        expect(screen.getByTestId("picked")).toHaveTextContent("EXISTING_KEY");
      },
    );
  });

  it("a vault that cannot be read shows its error even when nothing is picked", async () => {
    const refused = () =>
      fakeApi({
        credentials: vi.fn(async () => {
          throw new OmegaError({
            state: "refused",
            status: 403,
            message: "Credential vault is unavailable.",
          });
        }),
      });
    const api = refused();
    const first = renderDeployments(<Harness />, {
      api,
      route: "/dashboard/deployments/new",
    });
    expect(screen.getByTestId("secrets-field")).toBeInTheDocument();
    expect(await screen.findByTestId("secrets-unavailable")).toHaveTextContent(
      "Credential vault is unavailable.",
    );
    expect(screen.getByTestId("secrets-field")).toBeInTheDocument();
    expect(api.credentials).toHaveBeenCalled();
    first.unmount();

    renderDeployments(<Harness initial={["A_KEY"]} />, {
      api: refused(),
      route: "/dashboard/deployments/new",
    });
    expect(await screen.findByTestId("secrets-unavailable")).toHaveTextContent(
      "Credential vault is unavailable.",
    );
  });
});
