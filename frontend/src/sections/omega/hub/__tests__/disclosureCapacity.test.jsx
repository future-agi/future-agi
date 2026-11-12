import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import DisclosureEditor from "../components/DisclosureEditor";
import DisclosurePanel from "../components/DisclosurePanel";
import { disclosureShapeErrors } from "../lib/disclosure";
import { decisionPayload } from "../lib/decision";

vi.mock("../components/HubStates", () => ({
  // eslint-disable-next-line react/prop-types
  HubSection: ({ children }) => <section>{children}</section>,
}));

afterEach(cleanup);
const disclosure = {
  summary: "A complete disclosure confirmed in review.",
  network: "None.",
  files: "None.",
  shell: "None.",
  background: "None.",
  credentials: "None.",
};
const expanded = ["network", "files", "credentials"];
const LIMITS = {
  summary: 200,
  network: 1200,
  files: 1200,
  shell: 300,
  background: 300,
  credentials: 1200,
};

describe("Disclosure capacity", () => {
  it.each(expanded)(
    "accepts 300, 301, 900 and 1200 characters for %s; rejects 1201",
    (field) => {
      const sized = (length) => ({
        ...disclosure,
        [field]: "x".repeat(length),
      });
      for (const length of [300, 301, 900, 1200]) {
        expect(disclosureShapeErrors(sized(length))).toEqual({});
      }
      expect(disclosureShapeErrors(sized(1201))[field]).toBeTruthy();
    },
  );

  it.each(["summary", "shell", "background"])(
    "keeps the existing %s bound",
    (field) => {
      const max = field === "summary" ? 200 : 300;
      const sized = (length) => ({
        ...disclosure,
        [field]: "x".repeat(length),
      });
      expect(disclosureShapeErrors(sized(max))).toEqual({});
      expect(disclosureShapeErrors(sized(max + 1))[field]).toBeTruthy();
    },
  );

  it("preserves full confirmed text in the review decision payload", () => {
    const value = {
      ...disclosure,
      network: "x".repeat(1200),
      files: "y".repeat(900),
      credentials: "z".repeat(900),
    };
    const payload = decisionPayload({
      decision: "approve",
      rulesVersion: 1,
      disclosure: value,
    });
    expect(payload.disclosure).toEqual(value);
  });

  // The review page shows each part as a row with its own Edit; the limits
  // sit on the boxes Edit opens.
  it("sets reviewer input limits per category without truncating existing text", () => {
    render(
      <DisclosureEditor
        value={{ ...disclosure, network: "x".repeat(900) }}
        onChange={() => {}}
      />,
    );
    for (const button of screen.getAllByRole("button", { name: /^Edit / }))
      fireEvent.click(button);
    for (const field of Object.keys(disclosure)) {
      const input = screen.getByTestId(`hub-disclosure-${field}-input`);
      expect(input.maxLength).toBe(LIMITS[field]);
    }
    expect(
      screen.getByTestId("hub-disclosure-network-input").value,
    ).toHaveLength(900);
  });

  it.each(expanded)(
    "collapses long %s text with an accessible Show all control",
    (field) => {
      const text =
        "Contact and data details. ".repeat(30) +
        "Complete final disclosure sentence.";
      render(<DisclosurePanel disclosure={{ ...disclosure, [field]: text }} />);
      const toggle = screen.getByRole("button", {
        name: new RegExp(`Show all.*${field}`, "i"),
      });
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      const region = document.getElementById(
        toggle.getAttribute("aria-controls"),
      );
      expect(region).not.toBeNull();
      expect(region.hidden).toBe(true);
      fireEvent.click(toggle);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(region.hidden).toBe(false);
      expect(region.textContent).toBe(text);
      expect(screen.getByRole("button", { name: /Show less/i })).toBe(toggle);
      fireEvent.click(toggle);
      expect(region.hidden).toBe(true);
      // Native button semantics supply Enter/Space activation and keyboard focus.
      expect(toggle.tagName).toBe("BUTTON");
      toggle.focus();
      expect(document.activeElement).toBe(toggle);
    },
  );

  it("expands with Enter and collapses with Space while keeping keyboard focus", async () => {
    const user = userEvent.setup();
    render(
      <DisclosurePanel
        disclosure={{ ...disclosure, network: "x".repeat(900) }}
      />,
    );
    await user.tab();
    const toggle = screen.getByRole("button", { name: /Show all network/i });
    expect(document.activeElement).toBe(toggle);
    await user.keyboard("{Enter}");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await user.keyboard(" ");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(toggle);
  });

  it("leaves short disclosure parts visible without an expansion control", () => {
    render(<DisclosurePanel disclosure={disclosure} />);
    expect(screen.queryByRole("button", { name: /Show all/i })).toBeNull();
    expect(screen.getAllByText("None.")).toHaveLength(5);
  });
});
