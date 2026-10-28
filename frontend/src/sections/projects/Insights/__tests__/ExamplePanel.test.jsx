import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import ExamplePanel from "../ExamplePanel";
import { ReportProvider } from "../reportContext";
import { useCitedImage, useRunUnit } from "../insightsApi";

vi.mock("src/components/iconify", () => ({ default: () => null }));
const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock("react-router", () => ({ useNavigate: () => navigate }));
vi.mock("../insightsApi", () => ({
  useRunUnit: vi.fn(),
  useCitedImage: vi.fn(),
}));

const theme = createTheme({ palette: palette("light"), typography });

const report = {
  runId: "run-1",
  projectId: "project-1",
  unit: "sessions",
  categoriesById: new Map(),
  previews: new Map(),
  colorOf: () => "#888",
  openCategory: () => {},
};

// A long session as v2 prepare stores it: 30 numbered turns, the failure in
// turn 23.
const turns = Array.from({ length: 30 }, (_, i) => {
  const n = i + 1;
  return n === 23
    ? "[Turn 23] User: So will the export finish by tonight?\nAssistant: Yes, the export is running and will finish by tonight."
    : `[Turn ${n}] User: Is field ${n} included?\nAssistant: Yes, field ${n} is part of the default export template.`;
}).join("\n\n");

const unit = (overrides = {}) => ({
  unit_id: "sess_028_029",
  label: "",
  user_id: "cust_006",
  time: "2026-10-23T04:12:00Z",
  summary: "The agent said the export was running after the check failed.",
  lens_answer: "",
  facets: {},
  metrics: { turns: 30 },
  category_id: "",
  sub_category_id: "",
  content: turns,
  ...overrides,
});

const renderPanel = (data, context = report) => {
  useRunUnit.mockReturnValue({ data, isLoading: false, isError: false });
  return render(
    <ThemeProvider theme={theme}>
      <ReportProvider value={context}>
        <ExamplePanel open unitId={data.unit_id} onClose={() => {}} />
      </ReportProvider>
    </ThemeProvider>,
  );
};

describe("ExamplePanel", () => {
  let offsetTop;

  beforeEach(() => {
    useCitedImage.mockReturnValue({ url: null, isError: false });
    // jsdom lays nothing out: a turn's offset is its number x 100px.
    offsetTop = vi
      .spyOn(HTMLElement.prototype, "offsetTop", "get")
      .mockImplementation(function offset() {
        const turn = this.dataset?.turn;
        if (turn) return Number(turn) * 100;
        return this.tagName === "MARK" ? 2350 : 0;
      });
  });

  afterEach(() => {
    offsetTop.mockRestore();
    vi.clearAllMocks();
  });

  it("shows a v2 session's conversation, opened at the turn where it broke", () => {
    renderPanel(
      unit({
        facets: {
          broke_at_turn: 23,
          evidence: [
            {
              kind: "text",
              evidence_id: "ev2|9|s70|output|0|54",
              quote: "Yes, the export is running and will finish by tonight.",
              span: "s70",
              field: "output",
              supports: "failure",
            },
            {
              kind: "text",
              evidence_id: "ev2|9|s72|output|1|18",
              quote: '"status": "failed"',
              span: "s72",
              field: "output",
              supports: "failure",
            },
          ],
        },
      }),
    );

    const box = screen.getByTestId("example-conversation");
    // Every turn with its speakers, not just the last answer.
    expect(box.textContent).toContain("[Turn 1] User: Is field 1 included?");
    expect(box.textContent).toContain("[Turn 30] User: Is field 30 included?");
    expect(within(box).getAllByText("Assistant:")).toHaveLength(30);
    // The quote from turn 23 is marked in place; the tool's isn't in the
    // conversation, so it is listed above it.
    expect(box.querySelector("mark").textContent).toBe(
      "Yes, the export is running and will finish by tonight.",
    );
    expect(screen.getByText("Evidence is highlighted")).toBeInTheDocument();
    expect(box.querySelector('[data-turn="23"]')).toHaveAttribute(
      "title",
      "The turn where it broke",
    );
    // The conversation box (not the panel) is scrolled to turn 23.
    expect(box.scrollTop).toBe(23 * 100 - 48);
  });

  it("says nothing is highlighted when no quote is found in the content", () => {
    renderPanel(
      unit({
        content: "[Turn 1] User: Checkout fails.\nAssistant: Clear your cache.",
        facets: {
          evidence: [
            { kind: "text", quote: "not in the conversation" },
            { kind: "media", media: "m1", observation: "Card declined." },
          ],
        },
      }),
    );
    expect(screen.queryByText("Evidence is highlighted")).toBeNull();
  });

  it("shows the image a finding rests on, with what the analysis saw", () => {
    useCitedImage.mockReturnValue({ url: "blob:shot", isError: false });
    renderPanel(
      unit({
        unit_id: "sess_028_008",
        content:
          "[Turn 1] User: Checkout fails every time, screenshot attached.\nAssistant: Payments can fail for many reasons.",
        facets: {
          evidence: [
            {
              kind: "media",
              evidence_id: "ev2m|7|m1|full",
              media: "m1",
              span: "s2",
              field: "input",
              observation: "A card-declined error, code 51.",
              supports: "failure",
            },
            {
              kind: "text",
              quote: "Payments can fail for many reasons.",
              supports: "failure",
            },
          ],
        },
      }),
    );

    expect(useCitedImage).toHaveBeenCalledWith("run-1", "sess_028_008", "m1");
    // The thumbnail says what the analysis saw, like the enlarged view.
    const thumbnail = screen.getByRole("img", {
      name: "A card-declined error, code 51.",
    });
    expect(thumbnail).toHaveAttribute("src", "blob:shot");
    expect(
      screen.getByText("A card-declined error, code 51."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Enlarge image" }));
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("img", {
        name: "A card-declined error, code 51.",
      }),
    ).toHaveAttribute("src", "blob:shot");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Close image" }),
    );
  });

  it("names an image the analysis didn't describe as cited evidence", () => {
    useCitedImage.mockReturnValue({ url: "blob:shot", isError: false });
    renderPanel(
      unit({ facets: { evidence: [{ kind: "media", media: "m1" }] } }),
    );
    expect(
      screen.getByRole("img", { name: "Image cited as evidence" }),
    ).toHaveAttribute("src", "blob:shot");
  });

  it("opens the example's user on the report's days, not on Today", () => {
    const window = ["2026-10-22T00:00:00+00:00", "2026-10-23T23:59:59+00:00"];
    renderPanel(unit(), { ...report, runWindow: window });
    fireEvent.click(screen.getByRole("button", { name: "Open user" }));
    const url = new URL(navigate.mock.calls[0][0], "https://app.test");
    expect(url.pathname).toBe("/dashboard/users/cust_006");
    const dates = JSON.parse(url.searchParams.get("sessionDateFilter"));
    expect(dates.dateOption).toBe("Custom");
    expect(dates.dateFilter.map((value) => new Date(value).getTime())).toEqual(
      window.map((value) => new Date(value).getTime()),
    );
  });

  it("says plainly when the image can't be shown", () => {
    useCitedImage.mockReturnValue({ url: null, isError: true });
    renderPanel(
      unit({
        facets: { evidence: [{ kind: "media", media: "m1" }] },
      }),
    );
    expect(
      screen.getByText(/This image can.t be shown here/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
