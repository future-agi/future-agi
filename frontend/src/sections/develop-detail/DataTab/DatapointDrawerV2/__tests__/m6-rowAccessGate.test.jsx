// M6 (W4 §9, the RowView integration): Edit, Annotate and + Split follow the
// dataset's edit access; a disabled Edit names the server's reason; the
// Activity badge counts the row's comment threads like the All chip.
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { activityBadge, rowAccessGate } from "../rowAccessGate";
import { RowHeaderActions } from "../RowViewHeader";

vi.mock("src/api/develop/dataset-versions", () => ({
  useDatasetVersions: () => ({ data: null }),
}));

const REASON =
  "Everyone in Support Agents has View access to this dataset. You need edit access. Ask Asha Rao or Ravi Menon.";
const atView = {
  can: { view: true, download: false, edit: false, run: false, manage: false },
  reason: (perm) => (perm === "edit" ? REASON : null),
};
const atEdit = {
  can: { view: true, download: true, edit: true, run: true, manage: false },
  reason: () => null,
};

describe("rowAccessGate", () => {
  it("a member at View: no Edit, no Annotate, the server's reason", () => {
    expect(rowAccessGate({ access: atView })).toEqual({
      canEdit: false,
      annotate: false,
      editReason: REASON,
    });
  });

  it("edit access (or no access read yet) keeps today's behaviour", () => {
    expect(rowAccessGate({ access: atEdit })).toEqual({
      canEdit: true,
      annotate: true,
      editReason: null,
    });
    expect(rowAccessGate({ access: null }).canEdit).toBe(true);
    expect(rowAccessGate({ access: atEdit, asOf: true })).toMatchObject({
      canEdit: false,
      annotate: true,
    });
    expect(rowAccessGate({ access: atEdit, pendingDelete: true }).canEdit).toBe(
      false,
    );
  });

  it("a workspace viewer sees no access reason (the view-only banner says it)", () => {
    expect(rowAccessGate({ viewer: true, access: atView })).toEqual({
      canEdit: false,
      annotate: false,
      editReason: null,
    });
  });
});

describe("activityBadge", () => {
  it("adds the row's comment threads to N8's events and runs", () => {
    expect(activityBadge({ activity: 10, runs: 7 }, [{}])).toBe(18);
    expect(activityBadge({ activity: 10, runs: 7 }, undefined)).toBe(17);
    expect(activityBadge({}, [{}])).toBe(0);
  });
});

describe("RowHeaderActions", () => {
  const actions = { copyLink: () => {} };
  it("without edit access shows a disabled Edit and no Annotate", () => {
    render(
      <RowHeaderActions
        actions={actions}
        labelled
        canEdit={false}
        editReason={REASON}
        viewer
      />,
    );
    const denied = screen.getByTestId("row-edit-denied");
    expect(denied.disabled).toBe(true);
    expect(screen.queryByTestId("row-edit-button")).toBeNull();
    expect(screen.queryByTestId("row-annotate-button")).toBeNull();
  });

  it("with edit access shows Edit and Annotate as before", () => {
    render(<RowHeaderActions actions={actions} labelled canEdit />);
    expect(screen.getByTestId("row-edit-button")).toBeTruthy();
    expect(screen.getByTestId("row-annotate-button")).toBeTruthy();
    expect(screen.queryByTestId("row-edit-denied")).toBeNull();
  });
});
