import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import CreateSandboxDrawer from "../create/CreateSandboxDrawer";
import { CAPS, fakeApi, renderSandboxes } from "./helpers";
import { workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

function open(caps = CAPS, api = fakeApi(), onCreated = vi.fn()) {
  renderSandboxes(
    <CreateSandboxDrawer
      open
      caps={caps}
      onClose={vi.fn()}
      onCreated={onCreated}
    />,
    { api },
  );
  return { api, onCreated };
}

describe("Create sandbox drawer", () => {
  it("keeps the network off by default and explains what a policy can allow", () => {
    open();
    const toggle = screen.getByTestId("create-network-toggle");
    expect(toggle).not.toBeChecked();
    expect(toggle).not.toBeDisabled();
    // Explain the policy choices rather than repeat the legacy provider reason.
    const egress = screen.getByTestId("create-egress-reason").textContent;
    expect(egress).toBe(
      "A policy can allow all public hosts, or only listed IPv4 address ranges, optionally limited to TCP or UDP and then to specific ports. This deployment sets the widest mode you can use. Cloud metadata, private and link-local addresses, the sandbox host and other sandboxes stay blocked in every mode.",
    );
    expect(egress).not.toContain(CAPS.egressAllowList.reason);
    expect(egress).not.toMatch(/provider|operator/i);
    expect(screen.getByTestId("create-network-explain").textContent).toMatch(
      /no network interface besides loopback/,
    );
  });

  it("describes deployment network controls when networking is selected", () => {
    open();
    const toggle = screen.getByTestId("create-network-toggle");
    expect(toggle).not.toBeDisabled();
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(screen.getByTestId("create-network-explain").textContent).toBe(
      "With networking selected, outbound access depends on this deployment's network controls. Where per-sandbox network policy is enabled, sandboxes created here start with outbound IP traffic blocked until you set an allowed policy through the Sandboxes API.",
    );
    expect(screen.getByTestId("create-egress-reason").textContent).toBe(
      "A policy can allow all public hosts, or only listed IPv4 address ranges, optionally limited to TCP or UDP and then to specific ports. This deployment sets the widest mode you can use. Cloud metadata, private and link-local addresses, the sandbox host and other sandboxes stay blocked in every mode.",
    );
    const drawerText = screen.getByTestId("create-sandbox-drawer").textContent;
    expect(drawerText).not.toContain("can reach the internet");
    expect(drawerText).not.toContain("open outbound access");
  });

  it("greys out the network when the provider offers none", () => {
    open({ ...CAPS, networkModes: ["none"] });
    expect(screen.getByTestId("create-network-toggle")).toBeDisabled();
    expect(
      screen.getByText(
        /This workspace offers sandboxes without a network only/,
      ),
    ).toBeInTheDocument();
  });

  it("says network on cannot run on this deployment when EE names a host reason, in Future AGI's words", () => {
    // EE-SBX-SAFE-DEFAULTS: on a sandbox host without gVisor, caps leave `full`
    // out with a reason code and a reason written for whoever runs the deployment.
    const reason =
      "This sandbox has network on, and network-on sandboxes run only under the gVisor runtime (runsc), which this sandbox host does not have, so nothing was started. Sandboxes with network off are not affected; an operator can install gVisor on the sandbox host.";
    open({
      ...CAPS,
      networkModes: ["none"],
      networkUnavailableCode: "runsc_required",
      networkUnavailableReason: reason,
    });
    const toggle = screen.getByTestId("create-network-toggle");
    expect(toggle).toBeDisabled();
    expect(toggle).not.toBeChecked();
    const explain = screen.getByTestId("create-network-explain").textContent;
    expect(explain).toBe(
      "Sandboxes with the network on can't run on this deployment right now, so new sandboxes here start with the network off: no network interface besides loopback.",
    );
    // Not the workspace-settings sentence: this workspace's settings allow it.
    expect(explain).not.toMatch(/This workspace offers/);
    // The Network group never shows EE's operator sentence (COPY1). The Secrets
    // group's "yet" is HIDE-UNFINISHED-FA's to remove, so it is not checked here.
    expect(screen.getByTestId("create-network").textContent).not.toMatch(
      /operator|provider|runsc|yet\b/i,
    );
    expect(document.body.textContent).not.toContain(reason);
  });

  it("creates with the chosen limits inside the caps and hands back the new sandbox", async () => {
    const { api, onCreated } = open();
    fireEvent.change(screen.getByTestId("create-name"), {
      target: { value: "etl" },
    });
    fireEvent.change(screen.getByTestId("create-cpus"), {
      target: { value: "0.5" },
    });
    fireEvent.click(screen.getByTestId("create-network-toggle"));
    fireEvent.click(screen.getByTestId("create-submit"));
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    expect(api.create).toHaveBeenCalledWith(
      {
        name: "etl",
        image: "alpine:latest",
        limits: { cpus: 0.5, memoryMb: 512, pidsLimit: 256 },
        network: "full",
        idleTtlSeconds: 1800,
      },
      { idempotencyKey: expect.stringMatching(/^fa-sbx-/) },
    );
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith(
        expect.objectContaining({ id: "sbx_new0000000000000000000000" }),
      ),
    );
  });

  it("re-sends a create once after a lost assertion, with the same Idempotency-Key", async () => {
    let calls = 0;
    const api = fakeApi({
      create: vi.fn(async () => {
        calls += 1;
        if (calls === 1)
          throw new OmegaError({
            state: "refused",
            status: 401,
            code: "unauthorized",
            message: null,
          });
        return { id: "sbx_once00000000000000000000", name: "x" };
      }),
    });
    const { onCreated } = open(CAPS, api);
    fireEvent.click(screen.getByTestId("create-submit"));
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(api.create).toHaveBeenCalledTimes(2);
    const [first, second] = api.create.mock.calls.map(
      (call) => call[1].idempotencyKey,
    );
    expect(first).toBe(second);
  });

  it("never re-sends a create the server refused for good", async () => {
    const api = fakeApi({
      create: vi.fn(async () => {
        throw new OmegaError({
          state: "refused",
          status: 403,
          code: "role_forbidden",
          message: "No.",
        });
      }),
    });
    open(CAPS, api);
    fireEvent.click(screen.getByTestId("create-submit"));
    expect(await screen.findByTestId("create-error")).toHaveTextContent("No.");
    expect(api.create).toHaveBeenCalledTimes(1);
  });

  it("refuses a limit above the provider cap before calling the API", () => {
    const { api } = open();
    fireEvent.change(screen.getByTestId("create-memoryMb"), {
      target: { value: "4096" },
    });
    fireEvent.click(screen.getByTestId("create-submit"));
    expect(screen.getByText("Between 64 and 512")).toBeInTheDocument();
    expect(api.create).not.toHaveBeenCalled();
  });

  it("shows the server's refusal, such as the active-sandbox quota", async () => {
    const api = fakeApi({
      create: vi.fn(async () => {
        throw new OmegaError({
          state: "error",
          status: 409,
          code: "quota_exceeded",
          message: "This workspace already has 10 active sandboxes.",
        });
      }),
    });
    open(CAPS, api);
    fireEvent.click(screen.getByTestId("create-submit"));
    expect(await screen.findByTestId("create-error")).toHaveTextContent(
      "This workspace already has 10 active sandboxes.",
    );
  });

  it("offers Environment only when the workspace has a container environment to start from", async () => {
    const { api } = open();
    await waitFor(() => expect(api.environments).toHaveBeenCalled());
    expect(screen.queryByTestId("create-source-environment")).toBeNull();
    expect(
      screen.queryByText("This workspace has no container environments yet."),
    ).toBeNull();
  });

  it("offers Environment once an active container environment exists", async () => {
    const api = fakeApi({
      environments: vi.fn(async () => [
        {
          id: "env_1",
          name: "Python data",
          sandboxKind: "container",
          status: "active",
        },
      ]),
    });
    open(CAPS, api);
    expect(
      await screen.findByTestId("create-source-environment"),
    ).toBeInTheDocument();
  });

  it("has no Secrets section: the sandbox API attaches none", () => {
    open();
    expect(screen.queryByTestId("create-secrets")).toBeNull();
    expect(screen.queryByText("Secrets")).toBeNull();
    expect(screen.queryByText(/\byet\b/)).toBeNull();
  });
});
