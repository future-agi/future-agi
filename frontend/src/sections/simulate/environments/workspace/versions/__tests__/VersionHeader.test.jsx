import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import VersionHeader from "../VersionHeader";

const version = (extra = {}) => ({
  id: "e3",
  label: "v3",
  status: "active",
  createdAt: "2026-11-14T09:00:00Z",
  ...extra,
});

describe("VersionHeader", () => {
  it("marks a version saved while the check was unavailable", () => {
    render(<VersionHeader version={version({ savedWithoutCheck: true })} />);

    expect(screen.getByText("Saved without a check")).toBeInTheDocument();
  });

  it("marks nothing on a checked version", () => {
    render(<VersionHeader version={version()} />);

    expect(screen.queryByText("Saved without a check")).not.toBeInTheDocument();
  });
});
