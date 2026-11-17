import React from "react";
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderPage } from "../../__tests__/helpers";
import CheckField from "../CheckFields";

const field = {
  key: "mode",
  label: "Policy mode",
  type: "select",
  closedOptions: true,
  options: ["allowlist", "denylist", "audit"],
  helperText: "Choose the tool policy.",
};

describe("composed stored-policy guidance", () => {
  it("keeps invalid-mode repair guidance ahead of ordinary field help", () => {
    renderPage(<CheckField field={field} value="sync" onChange={() => {}} />);
    expect(screen.getByText(/Invalid stored setting/)).toBeInTheDocument();
    expect(screen.queryByText(field.helperText)).not.toBeInTheDocument();
  });

  it("keeps omitted-mode guidance rather than implying a default", () => {
    renderPage(<CheckField field={field} onChange={() => {}} />);
    expect(screen.getByText(/No policy mode is stored/)).toBeInTheDocument();
    expect(screen.queryByText(field.helperText)).not.toBeInTheDocument();
  });

  it("still shows ordinary field help for a valid policy", () => {
    renderPage(<CheckField field={field} value="audit" onChange={() => {}} />);
    expect(screen.getByText(field.helperText)).toBeInTheDocument();
  });
});
