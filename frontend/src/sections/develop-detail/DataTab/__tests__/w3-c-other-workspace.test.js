// W3 integration (W3-11): the old link of a moved dataset says where it went.
import { describe, expect, it } from "vitest";
import { otherWorkspaceNotice } from "../TopBanner";

const SUPPORT = "ws-support";
const BILLING = "ws-billing";
const workspaces = [
  { id: SUPPORT, name: "Support Agents", display_name: "Support Agents" },
  { id: BILLING, name: "Billing Agents", display_name: "Billing Agents" },
];

describe("otherWorkspaceNotice", () => {
  it("names the workspace a moved dataset went to", () => {
    expect(
      otherWorkspaceNotice({
        datasetId: "d1",
        currentWorkspaceId: SUPPORT,
        names: [
          {
            dataset_id: "d1",
            workspace_id: BILLING,
            moved_from_workspace_id: SUPPORT,
          },
        ],
        workspaces,
      }),
    ).toEqual({
      workspaceId: BILLING,
      workspaceName: "Billing Agents",
      moved: true,
      text: "This dataset moved to Billing Agents",
    });
  });

  it("a dataset made in the other workspace is 'in' it; none for this workspace", () => {
    expect(
      otherWorkspaceNotice({
        datasetId: "d2",
        currentWorkspaceId: SUPPORT,
        names: [{ datasetId: "d2", workspace_id: BILLING }],
        workspaces,
      })?.text,
    ).toBe("This dataset is in Billing Agents");
    expect(
      otherWorkspaceNotice({
        datasetId: "d1",
        currentWorkspaceId: BILLING,
        names: [{ dataset_id: "d1", workspace_id: BILLING }],
        workspaces,
      }),
    ).toBeNull();
    expect(
      otherWorkspaceNotice({
        datasetId: "d1",
        currentWorkspaceId: SUPPORT,
        names: [],
        workspaces,
      }),
    ).toBeNull();
  });
});
