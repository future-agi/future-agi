/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import KnowledgeBaseView from "./KnowledgeBaseView";

const auth = vi.hoisted(() => ({ role: "Viewer" }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: auth.role }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  handleOnDocsClicked: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../develop-detail/Common/PageHeadings", () => ({
  default: ({ title }) => <h1>{title}</h1>,
}));
vi.mock("./help/InfoKnowledgeModal", () => ({ default: () => null }));
vi.mock("./CreateKnowledgeBase/CreateKnowledgeBaseDrawer", () => ({
  default: () => null,
}));
// A workspace with no knowledge bases: the grid reports it has no data.
vi.mock("./KnowledgeBaseData/KnowledgeBaseData", async () => {
  const { forwardRef, useEffect } = await import("react");
  return {
    default: forwardRef(function KnowledgeBaseDataMock({ setHasData }, _ref) {
      useEffect(() => {
        setHasData(false);
      }, [setHasData]);
      return null;
    }),
  };
});

const REASON =
  "Viewers can't create knowledge bases. Ask a workspace admin for Member access.";

describe("KnowledgeBaseView — view-only teammates (W10-DESIGN-SYSTEM-U03)", () => {
  beforeEach(() => {
    auth.role = "Viewer";
  });

  it.each(["Viewer", "workspace_viewer"])(
    "tells a %s why Create knowledge base is disabled",
    (role) => {
      auth.role = role;
      render(<KnowledgeBaseView />);

      expect(
        screen.getByRole("button", { name: "Create knowledge base" }),
      ).toBeDisabled();
      expect(screen.getByText(REASON)).toBeInTheDocument();
    },
  );

  it("shows no reason when the teammate can create knowledge bases", () => {
    auth.role = "Member";
    render(<KnowledgeBaseView />);

    expect(
      screen.getByRole("button", { name: "Create knowledge base" }),
    ).toBeEnabled();
    expect(
      screen.queryByText(/can't create knowledge bases/),
    ).not.toBeInTheDocument();
  });
});
