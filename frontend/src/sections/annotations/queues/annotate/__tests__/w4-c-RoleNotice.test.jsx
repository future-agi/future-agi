import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import RoleNotice from "../RoleNotice";
import { useViewOnlyReviewer } from "../useViewOnlyReviewer";

// W4 C2: a workspace viewer inspects review items; members and editors score.
let role = "workspace_viewer";
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role }) }));

function Probe() {
  return <span data-testid="probe">{String(useViewOnlyReviewer())}</span>;
}

describe("RoleNotice", () => {
  beforeEach(() => {
    role = "workspace_viewer";
  });

  it("tells a workspace viewer they inspect and members score", () => {
    render(<RoleNotice />);
    expect(screen.getByTestId("role-notice").textContent).toBe(
      "View only · You can inspect these items; workspace members score them.",
    );
  });

  it.each(["workspace_member", "workspace_admin", "owner", undefined])(
    "shows nothing to %s",
    (r) => {
      role = r;
      render(<RoleNotice />);
      expect(screen.queryByTestId("role-notice")).toBeNull();
    },
  );

  it("view-only is true only for the workspace viewer role", () => {
    render(<Probe />);
    expect(screen.getByTestId("probe").textContent).toBe("true");
    role = "workspace_member";
    render(<Probe />);
    expect(screen.getAllByTestId("probe")[1].textContent).toBe("false");
  });
});
