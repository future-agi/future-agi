import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";

import { HeldBackView } from "../HeldBackList";

const selection = {
  requested: 10,
  running: 8,
  held_back: { count: 2, keys: ["refund-old", "draft-case"], by: { scenario_archived: 1, not_validated: 1 } },
  reasons: {
    scenario_archived: "Archived on 14 Nov 2026: old route",
    not_validated: "Not validated on these versions yet.",
  },
};

describe("Run dialog held-back list", () => {
  it("lists held-back reasons and the cases on request", async () => {
    const user = userEvent.setup();
    render(<HeldBackView selection={selection} />);
    const list = screen.getByTestId("held-back");
    expect(list).toHaveTextContent("8 of 10 cases will run.");
    expect(list).toHaveTextContent("1 held back: Archived on 14 Nov 2026: old route");
    expect(list).toHaveTextContent("1 held back: Not validated on these versions yet.");
    expect(screen.queryByTestId("held-back-keys")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show cases" }));
    expect(screen.getByTestId("held-back-keys")).toHaveTextContent("refund-old, draft-case");
  });

  it("hides when none", () => {
    render(<HeldBackView selection={{ ...selection, held_back: { count: 0, keys: [], by: {} } }} />);
    expect(screen.queryByTestId("held-back")).toBeNull();
  });
});
