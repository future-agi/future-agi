import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ProductionSourceList from "../ProductionSourceList";

afterEach(cleanup);
it("selects every matching trace in a session without selecting another session", () => {
  const onChange = vi.fn();
  const items = [
    { span_id: "a", session_id: "one", name: "first" },
    { span_id: "b", session_id: "one", name: "second" },
    { span_id: "c", session_id: "two", name: "other" },
  ];
  render(
    <ProductionSourceList
      items={items}
      kind="sessions"
      picked={[]}
      onChange={onChange}
    />,
  );
  fireEvent.click(screen.getByLabelText("Session one (2 traces)"));
  expect(onChange).toHaveBeenCalledWith(["a", "b"]);
  expect(screen.getByLabelText("Session two (1 trace)")).not.toBeChecked();
});
