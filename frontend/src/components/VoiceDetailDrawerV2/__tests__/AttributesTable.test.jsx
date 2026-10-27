import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import AttributesTable from "../AttributesTable";

// `Highlight` wraps matched tokens in their own <span>, so a path renders
// across several nodes. Match on the row element's combined textContent.
const hasRowText = (expected) => (_, el) => {
  if (!el) return false;
  const tag = el.tagName;
  if (tag !== "P" && tag !== "SPAN") return false;
  return el.textContent === expected;
};

const renderTable = (attributes) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <AttributesTable attributes={attributes} />
    </ThemeProvider>,
  );

// Shaped like a real voice payload: a flat dict of dotted keys.
const ATTRS = {
  "llm_token_usage.values": 1204,
  "call.participant_phone_number": "+15551234567",
  "call.status": "ended",
  "conversation.recording.mono.combined": "https://example.test/a.wav",
};

describe("AttributesTable search", () => {
  // Regression: `appliedQuery` used to be committed inside startTransition.
  // That update is interruptible, and the drawer re-rendered often enough to
  // restart it indefinitely — the query never reached the table, so typing
  // filtered nothing. It must be an urgent update behind the debounce only.
  it("applies the typed query to the table", async () => {
    const user = userEvent.setup();
    renderTable(ATTRS);

    await user.type(screen.getByPlaceholderText("Search attributes"), "llm");

    // Wait on a row DISAPPEARING — the matching key is present in tree mode
    // too, so its presence alone does not prove the filter ran.
    await waitFor(() => {
      expect(screen.queryByText(/participant_phone_number/)).toBeNull();
    });
    expect(screen.queryByText(/recording\.mono/)).toBeNull();
    expect(
      screen.getByText(hasRowText("llm_token_usage.values")),
    ).toBeInTheDocument();
  });

  it("restores every row when the query is cleared", async () => {
    const user = userEvent.setup();
    renderTable(ATTRS);
    const input = screen.getByPlaceholderText("Search attributes");

    await user.type(input, "llm");
    await waitFor(() => {
      expect(screen.queryByText(/participant_phone_number/)).toBeNull();
    });

    await user.clear(input);
    await waitFor(() => {
      expect(screen.getByText(/call\.status/)).toBeInTheDocument();
    });
  });

  it("reports no matches for a query nothing satisfies", async () => {
    const user = userEvent.setup();
    renderTable(ATTRS);

    await user.type(
      screen.getByPlaceholderText("Search attributes"),
      "zzz-nonexistent",
    );

    await waitFor(() => {
      expect(screen.getByText(/No matches for/i)).toBeInTheDocument();
    });
  });
});
