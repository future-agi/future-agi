import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useForm } from "react-hook-form";
import TaskSchedulingSection from "../TaskSchedulingSection";

let form;
const Harness = ({ defaults, isEdit = true }) => {
  form = useForm({
    defaultValues: { runType: "historical", spansLimit: 100000, ...defaults },
  });
  return <TaskSchedulingSection control={form.control} isEdit={isEdit} />;
};
Harness.propTypes = { defaults: PropTypes.object, isEdit: PropTypes.bool };

const windowText = () => screen.getByText(/^\(.+\)$/).textContent;

describe("TaskSchedulingSection preset", () => {
  it("writes the clicked preset into form state", () => {
    render(
      <Harness
        defaults={{
          datePreset: "Custom",
          startDate: "2026-06-01 00:00:00",
          endDate: "2026-07-01 00:00:00",
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "30D" }));
    expect(form.getValues().datePreset).toBe("30D");
  });

  // The stored key is authoritative — a Today window three days old reads as
  // Custom by shape, and nothing may overwrite the key with that guess.
  it("keeps the stored preset when the dates no longer imply it", () => {
    render(
      <Harness
        defaults={{
          datePreset: "Today",
          startDate: "2026-08-18 00:00:00",
          endDate: "2026-08-19 00:00:00",
        }}
      />,
    );
    expect(form.getValues().datePreset).toBe("Today");
  });

  it("renders the window beside the label, in brackets", () => {
    render(
      <Harness
        defaults={{
          datePreset: "12M",
          startDate: "2025-08-21 10:03:00",
          endDate: "2026-08-22 00:00:00",
        }}
      />,
    );
    expect(windowText()).toBe("(21 Aug 2025 – 21 Aug 2026)");
  });

  it("renders a single date for a Today window", () => {
    render(
      <Harness
        defaults={{
          datePreset: "Today",
          startDate: "2026-08-21 00:00:00",
          endDate: "2026-08-22 00:00:00",
        }}
      />,
    );
    expect(windowText()).toBe("(21 Aug 2026)");
  });

  it("renders on the create path too", () => {
    render(
      <Harness
        isEdit={false}
        defaults={{
          datePreset: "12M",
          startDate: "2025-08-21 10:03:00",
          endDate: "2026-08-22 00:00:00",
        }}
      />,
    );
    expect(windowText()).toBe("(21 Aug 2025 – 21 Aug 2026)");
  });
});
