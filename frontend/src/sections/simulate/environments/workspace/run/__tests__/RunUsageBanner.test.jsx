import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import RunUsageBanner from "../RunUsageBanner";

const at = (url) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <RunUsageBanner />
    </MemoryRouter>,
  );

describe("RunUsageBanner", () => {
  it("shows nothing on a real environment (there is no pre-run check yet)", () => {
    const { container } = at("/x");
    expect(container).toBeEmptyDOMElement();
  });

  it("holds the run with ?uiSample=usageheld, with Upgrade, flagged UI only", () => {
    at("/x?uiSample=usageheld");
    expect(
      screen.getByText("Run held: over your Voice simulation limit"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Lower the repeats or the scenarios to run, or upgrade\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upgrade" })).toHaveAttribute(
      "href",
      "/dashboard/settings/billing",
    );
    expect(screen.getByText("UI only")).toBeInTheDocument();
  });

  it("shows the budget line with ?uiSample=usage, and dismisses it", () => {
    at("/x?uiSample=usage");
    expect(
      screen.getByText(/past your 90% budget line\. It still runs\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Usage" })).toHaveAttribute(
      "href",
      "/dashboard/settings/usage-summary",
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/budget line/)).toBeNull();
  });
});
