import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

import ScenarioSuite from "./ScenarioSuite";

const mocks = vi.hoisted(() => ({ amend: vi.fn(), list: vi.fn() }));
vi.mock("src/api/harness/harness", () => ({
  amendHarnessScenarios: (...args) => mocks.amend(...args),
  listHarnessScenarios: (...args) => mocks.list(...args),
}));

const EDITING = {
  editable_fields: ["instruction", "tests", "max_turns", "background_noise"],
  applied_without_rework: ["tests", "max_turns", "background_noise"],
};

const scenario = (name, over = {}) => ({
  name,
  number: 1,
  use_case: "add an item to the cart",
  branch: `branch for ${name}`,
  tests: `what ${name} checks`,
  sub_goals: ["item_added"],
  background_noise: false,
  persona: {
    name: "Devon Reed",
    gender: "male",
    age_group: "25-32",
    occupation: "Technician",
    location: "United Kingdom",
    personality: "Analytical",
    communication_style: "Technical",
    accent: "Neutral",
    languages: ["English"],
    keywords: ["big mac"],
    initial_message: "Add one Big Mac please",
  },
  ...over,
});

// The suite comes from the endpoint, never from a prop, so a test says what the server answered.
const served = (rows, over = {}) => ({
  count: rows.length,
  total_pages: 1,
  results: rows,
  fields: [],
  scenario_editing: null,
  // The control's options are the server's, never the client's.
  groupings: [
    { value: "goal", label: "Use case" },
    { value: "accent", label: "Accent" },
    { value: "", label: "No grouping" },
  ],
  ...over,
});

const show = (ui) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      {ui}
    </QueryClientProvider>,
  );

describe("ScenarioSuite", () => {
  beforeEach(() => {
    mocks.amend.mockReset();
    mocks.list.mockReset();
    mocks.amend.mockResolvedValue({
      receipts: [{ outcome: "applied", why: "", scenario: "one" }],
    });
    mocks.list.mockResolvedValue(served([scenario("one")]));
  });

  it("removes a single scenario from its own row", async () => {
    const user = userEvent.setup();
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    await screen.findByText("one");
    await user.click(screen.getByRole("button", { name: /remove from this suite/i }));
    await waitFor(() => expect(mocks.amend).toHaveBeenCalledTimes(1));
    const [, changes, options] = mocks.amend.mock.calls[0];
    expect(changes).toEqual([{ op: "drop", scenario: "one" }]);
    // Nothing is left to prove once a scenario is gone, so this never costs a rework.
    expect(options).toEqual({ rework: false });
  });

  it("shows every field the design shows, and lets a person change only some", async () => {
    const user = userEvent.setup();
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable scenarioEditing={EDITING} />);
    await screen.findByText("one");
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));

    expect(screen.getByRole("textbox", { name: /passes when/i })).toBeEnabled();
    expect(screen.getByRole("combobox", { name: /personality/i })).toBeInTheDocument();

    expect(screen.getByRole("textbox", { name: /branch/i })).toBeDisabled();
    // Shown, and not changeable: the world is seeded around these, so an edit here alone would
    // leave the persona and the world disagreeing.
    expect(screen.getByDisplayValue("Devon Reed")).toBeDisabled();
    expect(screen.getByDisplayValue("25-32")).toBeDisabled();
    expect(screen.getByDisplayValue("male")).toBeDisabled();
    expect(screen.getByDisplayValue("Add one Big Mac please")).toBeDisabled();
    expect(screen.getByDisplayValue("add an item to the cart")).toBeDisabled();
  });

  it("opens the drawer rather than expanding the row", async () => {
    const user = userEvent.setup();
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable scenarioEditing={EDITING} />);
    await screen.findByText("one");
    expect(screen.queryByRole("presentation")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));
    // A drawer is a dialog over the table; the row it came from is still a single row.
    expect(await screen.findByRole("presentation")).toBeInTheDocument();
  });

  it("offers an edit even when the suite has no persona to edit", async () => {
    const user = userEvent.setup();
    const { persona, ...noCaller } = scenario("one");
    mocks.list.mockResolvedValue(served([noCaller]));
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    await screen.findByText("one");

    await user.click(screen.getByRole("button", { name: /edit scenario/i }));

    expect(screen.getByLabelText(/passes when/i)).toBeInTheDocument();
    // Nobody is on the other end, so the caller section is left out rather than shown empty.
    expect(screen.queryByLabelText(/personality/i)).not.toBeInTheDocument();
  });

  it("will not spend a re-check on a form nobody changed", async () => {
    const user = userEvent.setup();
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    await screen.findByText("one");
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));

    expect(screen.getByRole("button", { name: /save scenario/i })).toBeDisabled();
  });

  it("never sends the fields the world is seeded around", async () => {
    const user = userEvent.setup();
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable scenarioEditing={EDITING} />);
    await screen.findByText("one");
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));
    await user.type(screen.getByLabelText(/passes when/i), " and stays polite");
    await user.click(screen.getByRole("button", { name: /save scenario/i }));

    await waitFor(() => expect(mocks.amend).toHaveBeenCalledTimes(1));
    const [, changes] = mocks.amend.mock.calls[0];
    const persona = changes.find((one) => one.op === "set_persona")?.persona || {};
    // The caller's identity is seeded into the world, so editing it here would leave the two
    // disagreeing. The form must not offer it and the payload must not carry it.
    ["name", "gender", "age_group", "initial_message"].forEach((field) =>
      expect(persona).not.toHaveProperty(field),
    );
    expect(persona).toHaveProperty("personality");
    // The use case belongs to the contract and is shared, so it is never part of an edit.
    expect(changes.some((one) => one.field === "use_case")).toBe(false);
  });
});

// The suite can be far larger than a page, so narrowing it is the server's job. These say the
// browser only ever asks: nothing here filters, searches or groups a downloaded page.
describe("the narrowing is the server's", () => {
  beforeEach(() => {
    mocks.list.mockReset();
    mocks.list.mockResolvedValue(served([scenario("one")]));
  });

  it("asks for a page rather than the suite", async () => {
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    await screen.findByText("one");
    const [id, params] = mocks.list.mock.calls[0];
    expect(id).toBe("job-1");
    expect(params).toMatchObject({ page: 1, limit: 25 });
    // No grouping is named: omitting the key is how the client says the server decides.
    expect(params).not.toHaveProperty("group_by");
  });

  it("sends the search as a query param instead of matching in the browser", async () => {
    const user = userEvent.setup();
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    await screen.findByText("one");
    await user.type(screen.getByPlaceholderText(/search scenarios/i), "refund");
    await waitFor(() =>
      expect(
        mocks.list.mock.calls.some(([, params]) => params.search === "refund"),
      ).toBe(true),
    );
  });

  it("offers only the groupings the server named, and sends the choice as a query param", async () => {
    const user = userEvent.setup();
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    await screen.findByText("one");
    await user.click(screen.getByRole("combobox", { name: /group by/i }));
    // Grouping by persona NAME is not offered: unique first names make groups of one.
    expect(screen.queryByRole("option", { name: "Persona" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("option", { name: "Accent" }));
    await waitFor(() =>
      expect(
        mocks.list.mock.calls.some(([, params]) => params.group_by === "accent"),
      ).toBe(true),
    );
  });

  it("draws the sections the server sent, and counts none of them itself", async () => {
    mocks.list.mockResolvedValue(
      served(
        [
          { ...scenario("one"), group: "Booking" },
          { ...scenario("two"), group: "Cancelling" },
        ],
        {
          // The server orders the page by group and counts each section. The table walks them.
          groups: [
            { name: "Booking", count: 1 },
            { name: "Cancelling", count: 1 },
          ],
        },
      ),
    );
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    expect(await screen.findByText("Booking")).toBeInTheDocument();
    expect(screen.getByText("Cancelling")).toBeInTheDocument();
  });
});

describe("who decides what may be edited", () => {
  beforeEach(() => {
    mocks.list.mockReset();
    mocks.list.mockResolvedValue(served([scenario("one")]));
  });

  it("offers nothing when the job names no editable fields", async () => {
    const user = userEvent.setup();
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    await screen.findByText("one");
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));

    expect(screen.getByRole("textbox", { name: /passes when/i })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: /branch/i })).toBeDisabled();
  });

  it("takes the editable set from the endpoint, not from a prop", async () => {
    const user = userEvent.setup();
    mocks.list.mockResolvedValue(
      served([scenario("one")], { scenario_editing: { editable_fields: ["tests"] } }),
    );
    show(<ScenarioSuite scenarios={[]} jobId="job-1" editable />);
    await screen.findByText("one");
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));

    expect(screen.getByRole("textbox", { name: /passes when/i })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: /branch/i })).toBeDisabled();
  });
});
