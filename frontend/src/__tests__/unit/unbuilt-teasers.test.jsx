import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import LandingPageCard from "src/components/LandingPageCard/LandingPageCard";
import DatasetOptions from "src/sections/evals/AddDatasetDrawer/DatasetOptions";
import ConnectorView from "src/sections/sync/Connectors/ConnectorView";
import { OTHER_AUTH_METHODS } from "src/sections/agents/constants";

vi.mock("src/components/image", () => ({ default: () => null }));

const card = {
  title: "Unbuilt feature",
  description: "Teaser description",
  image: "/test.svg",
};

describe("Unbuilt features are absent rather than teased", () => {
  it("hides an unavailable landing action and its entire card", () => {
    const { container } = render(<LandingPageCard {...card} showAction />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(card.title)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps informational landing cards without an action", () => {
    render(<LandingPageCard {...card} />);
    expect(screen.getByText(card.title)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps built landing actions", () => {
    render(<LandingPageCard {...card} showAction availableFeature />);
    expect(screen.getByRole("button", { name: "Get started" })).toBeEnabled();
  });

  it("hides disabled dataset options, including their descriptive copy", () => {
    const onClick = vi.fn();
    const { container } = render(
      <DatasetOptions
        title="Unbuilt import"
        subTitle="Teaser"
        disabled
        onClick={onClick}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(onClick).not.toHaveBeenCalled();
  });

  it("keeps live dataset options actionable", () => {
    const onClick = vi.fn();
    render(
      <DatasetOptions
        title="Import data"
        subTitle="From a file"
        onClick={onClick}
      />,
    );
    fireEvent.click(screen.getByText("Import data"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("offers only wired sync connectors", () => {
    render(<ConnectorView />);
    for (const name of ["Upload CSV", "Big Query", "Python Realtime"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.queryByText(/Databricks|Snowflakes/i)).toBeNull();
  });

  it("offers only implemented authentication choices", () => {
    expect(OTHER_AUTH_METHODS.map(({ value }) => value)).toEqual([
      "basicAuth",
      "bearerToken",
      "noAuth",
    ]);
  });
});
