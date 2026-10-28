import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import UiOnlyFlag from "../UiOnlyFlag";
import SoonChip from "../SoonChip";
import { UI_ONLY_FEATURES } from "../../uiOnlyFeatures";

describe("UiOnlyFlag", () => {
  it("says UI only", () => {
    render(<UiOnlyFlag />);
    expect(screen.getByText("UI only")).toBeInTheDocument();
  });

  it("shows the backend ask for a registered feature", async () => {
    const [feature, { ask }] = Object.entries(UI_ONLY_FEATURES)[0];
    render(<UiOnlyFlag feature={feature} />);
    fireEvent.mouseOver(screen.getByText("UI only"));
    expect(
      await screen.findByText(new RegExp(`Needs: ${ask}`)),
    ).toBeInTheDocument();
  });

  it("uses a feature's own note in place of the sample-data line", async () => {
    render(<UiOnlyFlag feature="compareRuns" />);
    fireEvent.mouseOver(screen.getByText("UI only"));
    const tip = await screen.findByText(/Needs: /);
    expect(tip.textContent).toMatch(UI_ONLY_FEATURES.compareRuns.note);
    expect(tip.textContent).not.toMatch(/sample data/);
  });

  it("keeps the plain tooltip for an unregistered feature", async () => {
    render(<UiOnlyFlag feature="not-a-feature" />);
    fireEvent.mouseOver(screen.getByText("UI only"));
    const tip = await screen.findByText(/isn't wired to the API yet/);
    expect(tip.textContent).not.toMatch(/Needs:/);
  });
});

describe("UI_ONLY_FEATURES", () => {
  it("gives every entry a label and an ask", () => {
    const entries = Object.entries(UI_ONLY_FEATURES);
    expect(entries.length).toBeGreaterThan(0);
    entries.forEach(([, f]) => {
      expect(f.label).toEqual(expect.any(String));
      expect(f.ask).toEqual(expect.any(String));
      expect(f.ask).not.toMatch(/—/);
    });
  });
});

describe("SoonChip", () => {
  it("names the field that's coming", () => {
    render(<SoonChip label="Owner" what="Owner" />);
    expect(screen.getByText("Owner soon")).toBeInTheDocument();
  });

  it("says a field isn't in the API yet", async () => {
    render(<SoonChip label="Owner" what="Owner" />);
    fireEvent.mouseOver(screen.getByText("Owner soon"));
    expect(
      await screen.findByText("Owner isn't in the API yet"),
    ).toBeInTheDocument();
  });

  it("says plural fields aren't in the API yet", async () => {
    render(<SoonChip label="Count" what="Status counts" plural />);
    fireEvent.mouseOver(screen.getByText("Count soon"));
    expect(
      await screen.findByText("Status counts aren't in the API yet"),
    ).toBeInTheDocument();
  });

  it("says it's new in this design and nothing that showed before is missing", async () => {
    render(<SoonChip label="Owner" what="Owner" />);
    fireEvent.mouseOver(screen.getByText("Owner soon"));
    expect(
      await screen.findByText(
        "New in this design. Nothing that showed here before is missing; it fills in once the API sends it.",
      ),
    ).toBeInTheDocument();
  });

  it("explains the field itself when given a detail", async () => {
    render(
      <SoonChip
        label="Tiers"
        what="Tool tiers"
        plural
        detail="How each tool answers in a run: Fixed, Recorded, Simulated or Live."
      />,
    );
    fireEvent.mouseOver(screen.getByText("Tiers soon"));
    expect(
      await screen.findByText("How each tool answers in a run: Fixed, Recorded, Simulated or Live."),
    ).toBeInTheDocument();
  });
});
