import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { render } from "src/utils/test-utils";

import StageOutput from "./StageOutput";

describe("StageOutput", () => {
  it("shows the useful contract summary while keeping raw JSON collapsed", async () => {
    const user = userEvent.setup();
    render(
      <StageOutput
        output={{
          kind: "contract",
          title: "Agent contract",
          summary: "Contract ready",
          data: {
            one_liner: "Books rides for callers",
            modality: "voice",
            internal_debug_field: "only visible on demand",
            tools: [{ name: "book_ride" }],
          },
        }}
      />,
    );

    expect(screen.getByText("Books rides for callers")).toBeVisible();
    expect(screen.getByText("book_ride")).toBeVisible();
    expect(screen.getByText("View raw details")).toBeVisible();
    expect(screen.getByText(/internal_debug_field/)).not.toBeVisible();

    await user.click(screen.getByText("View raw details"));

    expect(screen.getByText(/internal_debug_field/)).toBeVisible();
  });
});
