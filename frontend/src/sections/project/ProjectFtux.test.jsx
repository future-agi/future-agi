import { describe, it, expect, vi } from "vitest";
import { renderWithRouter, screen } from "src/utils/test-utils";
import ProjectFtux from "./ProjectFtux";
import { PROJECT_FTUX_COPY, PROJECT_LIST_SUBTITLE } from "./common";

vi.mock("./NewProject/NewObserve", () => ({ default: () => <div /> }));
vi.mock("./NewProject/NewExperiment", () => ({ default: () => <div /> }));

const PROMISES_A_CREATE_ACTION = /create a project/i;

describe("ProjectFtux", () => {
  it("greets an Observe user on the observe route", () => {
    renderWithRouter(<ProjectFtux />, { route: "/dashboard/observe" });

    expect(
      screen.getByText(PROJECT_FTUX_COPY.observe.title),
    ).toBeInTheDocument();
    expect(
      screen.getByText(PROJECT_FTUX_COPY.observe.description),
    ).toBeInTheDocument();
  });

  it("greets a Prototype user on its own route", () => {
    renderWithRouter(<ProjectFtux />, { route: "/dashboard/prototype" });

    expect(
      screen.getByText(PROJECT_FTUX_COPY.experiment.title),
    ).toBeInTheDocument();
  });

  it("never promises a create action, since the screen has none", () => {
    renderWithRouter(<ProjectFtux />, { route: "/dashboard/observe" });

    expect(
      screen.queryByText(PROMISES_A_CREATE_ACTION),
    ).not.toBeInTheDocument();
  });
});

describe("project screen copy", () => {
  it("promises no create action on either surface", () => {
    [
      PROJECT_FTUX_COPY.observe.description,
      PROJECT_FTUX_COPY.experiment.description,
      PROJECT_LIST_SUBTITLE,
    ].forEach((copy) => expect(copy).not.toMatch(PROMISES_A_CREATE_ACTION));
  });
});
