import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ArchiveConfirmContent, { protectedArchiveSentence } from "./archiveCopy";

describe("ArchiveConfirmContent (UXF-09)", () => {
  it("keeps the label sentence and adds who may archive when a protected label points at a version", () => {
    render(<ArchiveConfirmContent labels={[{ name: "production", version: 2, isProtected: true }]} />);
    expect(screen.getByText("production points at v2. Agents following production will stop receiving this skill.")).toBeInTheDocument();
    expect(screen.getByText("Production is a protected label (only admins can move it), so only workspace admins can archive this skill.")).toBeInTheDocument();
    expect(document.body.textContent.replace(/protected labels?/gi, "").match(/protected/i)).toBeNull();
  });

  it("builds the sentence from the given protected names when labels don't carry it", () => {
    render(<ArchiveConfirmContent labels={[{ name: "production", version: 2 }, { name: "release-gate", version: 1 }]} protectedLabelNames={["production", "release-gate"]} />);
    expect(screen.getByText("Production and Release-gate are protected labels (only admins can move them), so only workspace admins can archive this skill.")).toBeInTheDocument();
  });

  it("claims nothing when no label is known to be protected", () => {
    render(<ArchiveConfirmContent labels={[{ name: "production", version: 2 }]} />);
    expect(screen.queryByTestId("archive-protected-rule")).not.toBeInTheDocument();
    expect(protectedArchiveSentence([{ name: "staging", version: 1, isProtected: false }], ["production"])).toBe("");
    expect(protectedArchiveSentence([], ["production"])).toBe("");
  });
});
