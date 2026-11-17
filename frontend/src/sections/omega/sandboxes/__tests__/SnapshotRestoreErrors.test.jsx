import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OmegaError, OMEGA_STATE } from "src/sections/omega/shared/omegaStates";

import SnapshotsTab from "../detail/SnapshotsTab";
import { fakeApi, renderSandboxes, sandboxView } from "./helpers";
import { workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

const SNAPSHOT = {
  id: "snap_0123456789abcdef01234567",
  sandboxId: "sbx_0123456789abcdef01234567",
  createdAt: "2026-10-31T12:00:00.000Z",
  kind: "files",
  statement: "Files only; memory and running services are not saved.",
  contentAddress: { artifactId: "art_1", sha256: "a".repeat(64) },
};

async function refuseRestore(error) {
  const api = fakeApi({
    snapshots: vi.fn(async () => [SNAPSHOT]),
    restore: vi.fn(async () => {
      throw error;
    }),
  });
  renderSandboxes(<SnapshotsTab sandbox={sandboxView({ network: "full" })} />, {
    api,
  });
  fireEvent.click(await screen.findByTestId("snapshot-restore"));
  fireEvent.click(screen.getByTestId("restore-confirm"));
  const message = await screen.findByTestId("restore-error");
  await waitFor(() => expect(api.restore).toHaveBeenCalledTimes(1));
  expect(api.restore).toHaveBeenCalledWith(
    SNAPSHOT.sandboxId,
    SNAPSHOT.id,
    { name: "data-cleanup (restored)" },
    { idempotencyKey: expect.stringMatching(/^fa-sbx-/) },
  );
  expect(screen.getByTestId("location").textContent).toBe("/dashboard/sandboxes");
  return message;
}

describe("Snapshot restore errors", () => {
  it.each(["egress_firewall_missing", "runsc_required", "sandbox_network_posture"])(
    "explains a %s refusal without claiming sandboxes are off",
    async (reason) => {
      const message = await refuseRestore(
        new OmegaError({
          state: OMEGA_STATE.UNAVAILABLE,
          status: 503,
          code: "sandboxes_unavailable",
          reason,
          message: "Sandboxes aren't turned on for this workspace.",
        }),
      );
      expect(message).toHaveTextContent(
        "Sandboxes with the network on can't start on this deployment right now, so nothing was started. Sandboxes with the network off are not affected.",
      );
      expect(message).not.toHaveTextContent("aren't turned on");
    },
  );

  it("preserves the existing message for a different restore error", async () => {
    const message = await refuseRestore(
      new OmegaError({
        state: OMEGA_STATE.UNAVAILABLE,
        status: 503,
        code: "sandboxes_unavailable",
        message: "Sandboxes aren't turned on for this workspace.",
      }),
    );
    expect(message).toHaveTextContent("Sandboxes aren't turned on for this workspace.");
  });
});
