import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import AttemptSelect from "../AttemptSelect";
import { ATTEMPT_COPY as C } from "../attempts.constants";

// As the call detail serves them: earlier attempts latest first, numbered by the server.
const EARLIER = [
  {
    attempt: "a2",
    number: 2,
    rerun_at: "2026-11-17T10:00:00+00:00",
    kept: true,
  },
  {
    attempt: "a1",
    number: 1,
    rerun_at: "2026-11-17T09:00:00+00:00",
    kept: true,
  },
];

describe("AttemptSelect", () => {
  it("shows nothing for a call that was never re-run", () => {
    const { container } = render(
      <AttemptSelect
        attemptNumber={1}
        earlierAttempts={[]}
        value={null}
        onChange={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("offers the latest attempt and each earlier one by the server's number, and gives back the id picked", () => {
    const onChange = vi.fn();
    render(
      <AttemptSelect
        attemptNumber={3}
        earlierAttempts={EARLIER}
        value={null}
        onChange={onChange}
      />,
    );
    const select = screen.getByRole("combobox", { name: C.label });
    expect(select).toHaveTextContent(C.latest(3));
    fireEvent.mouseDown(select);
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((one) => one.textContent)).toEqual([
      C.latest(3),
      expect.stringContaining("Attempt 2, re-run at"),
      expect.stringContaining("Attempt 1, re-run at"),
    ]);
    fireEvent.click(options[2]);
    expect(onChange).toHaveBeenCalledWith("a1");
  });
});
