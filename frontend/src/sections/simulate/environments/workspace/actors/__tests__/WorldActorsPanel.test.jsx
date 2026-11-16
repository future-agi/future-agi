import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import { CLEAN, cast, typedActor } from "./castFixtures";

const mutate = vi.fn();
const acceptMutate = vi.fn();
const resetEdit = vi.fn();
const refetch = vi.fn();
let editState;
let actorsData;
let actorsState;
// The form check's answer per step; undefined is still being asked.
let answer;
let asked;
// Save tests use the real hooks over a mocked axios; the rest use these stand-ins.
let realHooks = false;
let viewState;

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("src/api/simulate-environments/versions", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    useActors: (...args) =>
      realHooks
        ? real.useActors(...args)
        : { data: actorsData, refetch, ...actorsState },
    useEditActor: (...args) =>
      realHooks
        ? real.useEditActor(...args)
        : { mutate, reset: resetEdit, ...editState },
    useActorCheck: (...args) => {
      if (realHooks) return real.useActorCheck(...args);
      [, asked] = args;
      return {
        verdict: asked && answer ? answer : null,
        pending: Boolean(asked) && !answer,
        isError: false,
        error: null,
        retry: vi.fn(),
      };
    },
    useSetActorLimits: (...args) =>
      realHooks
        ? real.useSetActorLimits(...args)
        : { mutate: vi.fn(), reset: vi.fn() },
    useAcceptUnresolved: (...args) =>
      realHooks
        ? real.useAcceptUnresolved(...args)
        : { mutate: acceptMutate, reset: vi.fn(), isPending: false },
    useUploadEnvironmentFile: (...args) =>
      realHooks
        ? real.useUploadEnvironmentFile(...args)
        : { mutateAsync: vi.fn(), reset: vi.fn() },
    useActorView: () =>
      viewState ?? {
        data: {
          sees: [
            { input: "actor.goal", value: "Approve fast", text: "Approve fast" },
            {
              input: "actor.constraints",
              value: {
                may_agree: [],
                private: [],
                escalate_when: [],
                stop_when: [],
              },
              text: "",
            },
            {
              input: "actor.knowledge",
              value: [{ kind: "facts", text: "The limit is 25%" }],
              text: "The limit is 25%",
            },
            {
              input: "actor.behaviour",
              value: { response_timing: { kind: "delayed", seconds: 5 } },
              text: "Answers after 5 s",
            },
          ],
          never_sees: {
            scenario_fields: ["solution", "sub_goals"],
            files: ["sub_goals.json"],
            other_actors: [],
            grader: ["solution", "sub goals"],
            others: [{ fields: ["goal", "prompt"], count: 3 }],
          },
        },
      },
  };
});

const { default: axios } = await import("src/utils/axios");
const { actorsKey } = await import("src/api/simulate-environments/versions");
const { default: WorldActorsPanel } = await import("../WorldActorsPanel");

const MANAGER = typedActor({
  id: "manager",
  name: "Priya Raman",
  role: "sales manager",
  goal: "Approve only discounts she can justify",
  permitted_actions: ["crm.approve_discount"],
});

const choose = (label, option) => {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole("option", { name: option }));
};
const addActor = (side = "In the world") => {
  fireEvent.click(screen.getByRole("button", { name: "Add actor" }));
  fireEvent.click(screen.getByRole("menuitem", { name: new RegExp(side) }));
};
const saveButton = () => screen.getByRole("button", { name: "Save to draft" });
const sent = (call = 0) => mutate.mock.calls[call][0];

describe("WorldActorsPanel saving against the real hooks", () => {
  const DRAFT_CHANGED = {
    error: "draft_changed",
    message: "the actors were edited elsewhere since you read them",
    statusCode: 409,
  };
  const OPENED = cast([MANAGER]);
  const save = vi.fn();
  const upload = vi.fn();
  const VALIDATION =
    "/simulate/api/harness-environments/env-1/actor-validation/";
  const body = (call) => save.mock.calls[call][0];
  let client;

  const openEdit = async () => {
    render(
      <QueryClientProvider client={client}>
        <WorldActorsPanel envId="env-1" />
      </QueryClientProvider>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit Priya Raman" }),
    );
    fireEvent.change(screen.getByLabelText("Role (optional)"), {
      target: { value: "regional manager" },
    });
    // Save waits for the server's check of exactly this edit.
    await waitFor(() => expect(saveButton()).toBeEnabled(), {
      timeout: 5000,
    });
  };

  beforeEach(() => {
    realHooks = true;
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    axios.get.mockReset();
    axios.put.mockReset();
    axios.post.mockReset();
    save.mockReset();
    upload.mockReset();
    axios.get.mockResolvedValue({ data: OPENED });
    axios.put.mockImplementation((_url, sentBody) => save(sentBody));
    axios.post.mockImplementation((url, sentBody) =>
      url === VALIDATION ? Promise.resolve({ data: CLEAN }) : upload(sentBody),
    );
    return () => {
      realHooks = false;
    };
  });

  it("asks the server to check the edit, without writing, before Save is offered", async () => {
    await openEdit();
    const check = axios.post.mock.calls
      .filter(([url]) => url === VALIDATION)
      .at(-1);
    expect(check[1]).toEqual({
      op: "replace",
      actor_id: "manager",
      changes: { role: "regional manager" },
    });
    expect(save).not.toHaveBeenCalled();
  });

  it("sends the revision the form opened at, then saves the same changes at the fresh revision", async () => {
    const elsewhere = cast(
      [{ ...MANAGER, goal: "Approve only large discounts" }],
      { revision: 4 },
    );
    await openEdit();
    // A window-focus refetch moves the cast under the open form.
    axios.get.mockResolvedValue({ data: elsewhere });
    await client.invalidateQueries({ queryKey: actorsKey("env-1") });
    await screen.findByText(/Wants: Approve only large discounts/);
    save.mockRejectedValueOnce(DRAFT_CHANGED);

    fireEvent.click(saveButton());
    await screen.findByText(/changed elsewhere/);
    expect(body(0)).toEqual({
      changes: { role: "regional manager" },
      expected_revision: 3,
    });
    expect(screen.getByLabelText("Role (optional)")).toHaveValue(
      "regional manager",
    );

    save.mockResolvedValueOnce({ data: elsewhere });
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.queryByText("Save to draft")).toBeNull());
    // Only the edited field goes.
    expect(body(1)).toEqual({
      changes: { role: "regional manager" },
      expected_revision: 4,
    });
  });

  it("asks the server about a form as soon as it opens, before anything is typed", async () => {
    render(
      <QueryClientProvider client={client}>
        <WorldActorsPanel envId="env-1" />
      </QueryClientProvider>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit Priya Raman" }),
    );
    await waitFor(
      () =>
        expect(
          axios.post.mock.calls.find(([url]) => url === VALIDATION)?.[1],
        ).toEqual({ op: "replace", actor_id: "manager", changes: {} }),
      { timeout: 5000 },
    );
  });

  it("says when the check failed, keeps Save off, and asks again on Try again", async () => {
    let refuse = true;
    axios.post.mockImplementation((url, sentBody) =>
      url !== VALIDATION
        ? upload(sentBody)
        : refuse
          ? Promise.reject({
              statusCode: 400,
              error: "actor_check_refused",
              message: "The check was refused",
            })
          : Promise.resolve({ data: CLEAN }),
    );
    render(
      <QueryClientProvider client={client}>
        <WorldActorsPanel envId="env-1" />
      </QueryClientProvider>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit Priya Raman" }),
    );
    fireEvent.change(screen.getByLabelText("Role (optional)"), {
      target: { value: "regional manager" },
    });
    expect(
      await screen.findByText("The check was refused", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    expect(screen.queryByText("Checking…")).toBeNull();
    refuse = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(saveButton()).toBeEnabled(), { timeout: 5000 });
  });

  it("asks again when the cast is read again under an open form", async () => {
    await openEdit();
    const before = axios.post.mock.calls.filter(
      ([url]) => url === VALIDATION,
    ).length;
    await client.invalidateQueries({ queryKey: actorsKey("env-1") });
    await waitFor(() =>
      expect(
        axios.post.mock.calls.filter(([url]) => url === VALIDATION).length,
      ).toBeGreaterThan(before),
    );
  });

  it("sends one save for a double click, so the second click is never refused as someone else's edit", async () => {
    await openEdit();
    let answer;
    save.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    fireEvent.click(saveButton());
    fireEvent.click(saveButton());
    await waitFor(() => expect(save).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 20));
    answer({ data: cast([MANAGER], { revision: 4 }) });
    await waitFor(() => expect(screen.queryByText("Save to draft")).toBeNull());
    expect(save).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/changed elsewhere/)).toBeNull();
  });

  it("picks a document uploaded from the open form by where the server stored it, and saves at the upload's revision", async () => {
    const uploaded = cast([MANAGER], {
      revision: 4,
      knowledge_sources: { files: ["knowledge/policy-2.md"], records: [] },
      uploaded_path: "knowledge/policy-2.md",
    });
    upload.mockResolvedValueOnce({ data: uploaded });
    await openEdit();
    const picker = document.querySelector('input[type="file"]');
    fireEvent.change(picker, {
      target: {
        files: [new File(["# Policy"], "policy.md", { type: "text/markdown" })],
      },
    });
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    // Sent at the revision the form shows.
    expect(upload.mock.calls[0][0].get("expected_revision")).toBe("3");
    await screen.findByText("knowledge/policy-2.md");
    await waitFor(() => expect(saveButton()).toBeEnabled(), {
      timeout: 5000,
    });
    save.mockResolvedValueOnce({ data: uploaded });
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.queryByText("Save to draft")).toBeNull());
    expect(save).toHaveBeenCalledTimes(1);
    expect(body(0)).toEqual({
      changes: { role: "regional manager", files: ["knowledge/policy-2.md"] },
      expected_revision: 4,
    });
  });

  it.each([
    [
      {
        error: "upload_invalid",
        message: "notes.pdf: the format is one of .md, .txt, .json, .csv",
        statusCode: 400,
      },
      "notes.pdf: the format is one of .md, .txt, .json, .csv",
    ],
    [{ message: "Network Error" }, "The document could not be uploaded"],
  ])(
    "says why an upload was refused in the platform's words, never a transport message",
    async (failure, shown) => {
      upload.mockRejectedValueOnce(failure);
      await openEdit();
      fireEvent.change(document.querySelector('input[type="file"]'), {
        target: { files: [new File(["x"], "notes.pdf")] },
      });
      expect(await screen.findByText(shown)).toBeInTheDocument();
    },
  );

  it("moves the form onto the fresh record when an upload finds the draft moved, and uploads at its revision", async () => {
    await openEdit();
    const elsewhere = cast(
      [{ ...MANAGER, goal: "Approve only large discounts" }],
      { revision: 5 },
    );
    upload.mockRejectedValueOnce(DRAFT_CHANGED);
    axios.get.mockResolvedValue({ data: elsewhere });
    const file = () =>
      new File(["# Policy"], "policy.md", { type: "text/markdown" });
    fireEvent.change(document.querySelector('input[type="file"]'), {
      target: { files: [file()] },
    });
    await screen.findByText(/changed elsewhere while you were editing/);
    await screen.findByText(/Wants: Approve only large discounts/);
    // What was typed stays.
    expect(screen.getByLabelText("Role (optional)")).toHaveValue(
      "regional manager",
    );
    upload.mockResolvedValueOnce({
      data: cast([{ ...MANAGER, goal: "Approve only large discounts" }], {
        revision: 6,
        knowledge_sources: { files: ["knowledge/policy.md"], records: [] },
        uploaded_path: "knowledge/policy.md",
      }),
    });
    fireEvent.change(document.querySelector('input[type="file"]'), {
      target: { files: [file()] },
    });
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    expect(upload.mock.calls[1][0].get("expected_revision")).toBe("5");
    await screen.findByText("knowledge/policy.md");
    await waitFor(() => expect(saveButton()).toBeEnabled(), {
      timeout: 5000,
    });
    save.mockResolvedValueOnce({ data: cast([MANAGER], { revision: 7 }) });
    fireEvent.click(saveButton());
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(body(0).expected_revision).toBe(6);
  });

  it("names who reads a file of the same name and keeps the form's files as they were", async () => {
    upload.mockRejectedValueOnce({
      error: "upload_exists",
      message:
        "This environment already has a file named policy.md, which Dana Brooks reads; rename the file to upload it",
      readers: ["Dana Brooks"],
      statusCode: 409,
    });
    await openEdit();
    fireEvent.change(document.querySelector('input[type="file"]'), {
      target: { files: [new File(["# New"], "policy.md")] },
    });
    expect(
      await screen.findByText(/policy.md, which Dana Brooks reads/),
    ).toBeInTheDocument();
    expect(screen.queryByText("knowledge/policy.md")).toBeNull();
  });

  it("says so and won't save when the actor was removed elsewhere", async () => {
    await openEdit();
    save.mockRejectedValueOnce(DRAFT_CHANGED);
    axios.get.mockResolvedValue({ data: cast([], { revision: 4 }) });
    fireEvent.click(saveButton());
    await screen.findByText(/removed elsewhere/);
    expect(screen.getByLabelText("Role (optional)")).toHaveValue(
      "regional manager",
    );
    expect(saveButton()).toBeDisabled();
  });
});

describe("WorldActorsPanel", () => {
  beforeEach(() => {
    mutate.mockReset();
    resetEdit.mockReset();
    refetch.mockReset();
    editState = { isPending: false, error: null };
    actorsData = cast([MANAGER]);
    actorsState = { isLoading: false, isError: false };
    viewState = null;
    answer = CLEAN;
    asked = undefined;
  });

  it("shows a skeleton while loading, with no count and nothing to add yet", () => {
    actorsData = undefined;
    actorsState = { isLoading: true, isError: false };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the actors…");
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByText("Actors")).toBeInTheDocument();
    expect(screen.queryByText("Add actor")).toBeNull();
  });

  it("says the actors could not be loaded, offers a retry and nothing to add", () => {
    actorsData = undefined;
    actorsState = { isLoading: false, isError: true };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Couldn't load the actors")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
    expect(screen.queryByText("Add actor")).toBeNull();
  });

  it("keeps a cast already read when a refresh fails", () => {
    actorsState = { isLoading: false, isError: true };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Actors (1)")).toBeInTheDocument();
    expect(screen.getByText("Add actor")).toBeInTheDocument();
  });

  it("groups the cast by side, the caller's side first, each with its count, in the cast's order", () => {
    actorsData = cast([
      typedActor({ id: "caller", name: "Dana", side: "caller" }),
      typedActor({
        id: "colleague",
        name: "Jordan Taylor",
        side: "caller",
        channel: "audio_mix",
      }),
      MANAGER,
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Actors (3)")).toBeInTheDocument();
    const callerSide = screen.getByTestId("actors-side-caller");
    const world = screen.getByTestId("actors-side-world");
    expect(
      callerSide.compareDocumentPosition(world) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      within(callerSide).getByText("On the caller's side"),
    ).toBeInTheDocument();
    expect(within(callerSide).getByText("2")).toBeInTheDocument();
    const names = within(callerSide)
      .getAllByRole("button", { name: /^Edit / })
      .map((one) => one.getAttribute("aria-label"));
    expect(names).toEqual(["Edit Dana", "Edit Jordan Taylor"]);
    expect(within(world).getByText("In the world")).toBeInTheDocument();
    expect(within(world).getByText("1")).toBeInTheDocument();
    expect(within(world).getByText(/Priya Raman/)).toBeInTheDocument();
  });

  it("says when a side has no one yet", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(
      within(screen.getByTestId("actors-side-caller")).getByText(
        "No one here yet.",
      ),
    ).toBeInTheDocument();
  });

  it("starts an actor added on the caller's side on that side's default way of joining, and sends it", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    addActor("On the caller's side");
    expect(
      screen.getByLabelText("How they join the conversation"),
    ).toHaveTextContent("Heard nearby");
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Jordan" },
    });
    fireEvent.click(saveButton());
    expect(sent().changes).toMatchObject({
      side: "caller",
      channel: "audio_mix",
    });
  });

  it("asks which side before adding, and adds the actor on that side", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Add actor" }));
    const menu = screen.getByRole("menu");
    expect(
      within(menu).getByText(
        "Someone nearby, who takes the phone, or a second person in the chat",
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      within(menu).getByRole("menuitem", { name: /On the caller's side/ }),
    );
    expect(
      screen.getByText(
        "On the caller's side · added to this environment's draft",
      ),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Jordan" },
    });
    fireEvent.click(saveButton());
    expect(sent()).toMatchObject({
      op: "create",
      changes: { side: "caller", name: "Jordan" },
    });
  });

  it("lists each actor with what it may do, when it comes in and its fallback, as the cast states them", () => {
    actorsData = cast([
      {
        ...MANAGER,
        when_label: "When the agent calls crm.request_approval",
        fallback: {
          kind: "actor",
          value: "lead_one",
          label: "Sam Rivera answers instead",
        },
        scenarios: 2,
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    expect(
      screen.getByText(
        "May do: crm.approve_discount · Comes in: When the agent calls crm.request_approval · If they do not answer: Sam Rivera answers instead · In 2 scenarios",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
  });

  it("marks a permitted action this world no longer has", () => {
    actorsData = cast([
      {
        ...MANAGER,
        permitted_actions: ["crm.approve_discount", "crm.old"],
        permissions: [
          { action: "crm.approve_discount", in_world: true },
          { action: "crm.old", in_world: false },
        ],
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    expect(
      screen.getByText(
        /May do: crm.approve_discount, crm.old \(not an action of this world\)/,
      ),
    ).toBeInTheDocument();
  });

  it("shows how a speaking actor joins and how loud it is, beside what it may do", () => {
    actorsData = cast([
      typedActor({
        id: "colleague",
        name: "Jordan Taylor",
        side: "caller",
        channel: "audio_mix",
        scenarios: 2,
      }),
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    expect(
      screen.getByText(
        "Heard nearby · Loudness automatic · May do: Nothing in the world · Comes in: From the start · If they do not answer: Nothing happens · In 2 scenarios",
      ),
    ).toBeInTheDocument();
  });

  it("shows who can reach whom from each actor's reaches and when it comes in, once there is more than one actor", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.queryByText("Who can reach whom")).toBeNull();
    actorsData = cast([
      { ...MANAGER, reaches: ["caller"], when_label: "When contacted" },
      typedActor({ id: "caller", name: "Dana", side: "caller" }),
    ]);
    const { container } = render(<WorldActorsPanel envId="env-2" />);
    expect(
      within(container).getByText("Who can reach whom"),
    ).toBeInTheDocument();
    expect(
      within(container).getByText("Its actions reach Dana"),
    ).toBeInTheDocument();
    expect(
      within(container).getByText("Comes in: When contacted"),
    ).toBeInTheDocument();
    expect(
      within(container).getByRole("button", { name: "Limits" }),
    ).toBeInTheDocument();
    expect(within(container).queryByText("Save limits to draft")).toBeNull();
  });

  it("keeps limits typed but not saved when another change moves the cast", () => {
    const { rerender } = render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Limits" }));
    fireEvent.change(screen.getByLabelText("How many at once on a call"), {
      target: { value: "3" },
    });
    actorsData = cast([MANAGER, typedActor({ id: "ops", name: "Ops" })], {
      revision: 4,
    });
    rerender(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByLabelText("How many at once on a call")).toHaveValue(
      "3",
    );
  });

  it("says there is no caller only when the cast says so and has actors", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText(/no caller record yet/)).toBeInTheDocument();
    actorsData = cast([]);
    const empty = render(<WorldActorsPanel envId="env-2" />);
    expect(
      within(empty.container).queryByText(/no caller record yet/),
    ).toBeNull();
    actorsData = cast([MANAGER], { has_caller: true });
    const withCaller = render(<WorldActorsPanel envId="env-3" />);
    expect(
      within(withCaller.container).queryByText(/no caller record yet/),
    ).toBeNull();
  });

  it("shows what an actor sees and never sees in plain words", () => {
    render(<WorldActorsPanel envId="env-1" locked />);
    fireEvent.click(screen.getByText("What they see"));
    expect(
      screen.getByText("What they want: Approve fast"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("What they know: The limit is 25%"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("How they behave: Answers after 5 s"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Grader material: solution, sub goals"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "What the 3 other actors each keep to themselves: goal, prompt",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Hide what they see"));
    expect(screen.getByText("What they see")).toBeInTheDocument();
  });

  it("adds someone in the world with every field, sending only what was set, as typed changes", () => {
    actorsData = cast([], { source: "saved", revision: 0 });
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "manager" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya" },
    });
    fireEvent.click(screen.getByText("More details"));
    fireEvent.change(
      screen.getByLabelText("Is done when (optional, one per line)"),
      { target: { value: "She has decided" } },
    );
    fireEvent.change(
      screen.getByLabelText("What they know (optional, one per line)"),
      { target: { value: "The limit is 25%" } },
    );
    choose("Answers", "After a delay");
    fireEvent.change(screen.getByLabelText("Seconds"), {
      target: { value: "5" },
    });
    fireEvent.mouseDown(
      screen.getByLabelText("World actions they may take (optional)"),
    );
    expect(screen.getByText("Approve a pending discount")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("option", { name: /crm.approve_discount/ }),
    );
    choose("Comes in", "When the agent calls an action");
    choose("The action", "crm.request_approval");
    fireEvent.click(saveButton());
    expect(sent()).toEqual({
      op: "create",
      id: "manager",
      revision: 0,
      shareActions: [],
      changes: {
        side: "world",
        name: "Priya",
        stop_when: ["She has decided"],
        facts: ["The limit is 25%"],
        response_timing: { kind: "delayed", seconds: 5 },
        permitted_actions: ["crm.approve_discount"],
        trigger: { kind: "on_tool", tool: "crm.request_approval" },
      },
    });
  });

  it("shows the conflict the server finds before saving, and saves the action shared only once confirmed", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Lena" },
    });
    expect(screen.queryByTestId("actor-permission-conflict")).toBeNull();
    answer = {
      ...CLEAN,
      conflicts: [
        {
          actor: "new",
          action: "crm.approve_discount",
          holder: "manager",
          holder_name: "Priya Raman",
        },
      ],
    };
    fireEvent.mouseDown(
      screen.getByLabelText("World actions they may take (optional)"),
    );
    fireEvent.click(
      screen.getByRole("option", { name: /crm.approve_discount/ }),
    );
    const warning = screen.getByTestId("actor-permission-conflict");
    expect(warning).toHaveTextContent(
      "crm.approve_discount is held only by Priya Raman today",
    );
    expect(saveButton()).toBeDisabled();
    fireEvent.click(within(warning).getByLabelText("Both should have it"));
    fireEvent.click(saveButton());
    expect(sent().changes.permitted_actions).toEqual(["crm.approve_discount"]);
    expect(sent().shareActions).toEqual(["crm.approve_discount"]);
  });

  it("needs the confirmation again when the server finds another conflict after ticking", () => {
    const heldBy = (...actions) => ({
      ...CLEAN,
      conflicts: actions.map((action) => ({
        actor: "new",
        action,
        holder: "x",
        holder_name: "Ops Lead",
      })),
    });
    answer = heldBy("crm.approve_discount");
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Lena" },
    });
    const field = screen.getByLabelText(
      "World actions they may take (optional)",
    );
    fireEvent.mouseDown(field);
    fireEvent.click(
      screen.getByRole("option", { name: /crm.approve_discount/ }),
    );
    fireEvent.click(
      within(screen.getByTestId("actor-permission-conflict")).getByLabelText(
        "Both should have it",
      ),
    );
    expect(saveButton()).not.toBeDisabled();
    answer = heldBy("crm.approve_discount", "crm.request_approval");
    fireEvent.mouseDown(field);
    fireEvent.click(
      screen.getByRole("option", { name: /crm.request_approval/ }),
    );
    expect(saveButton()).toBeDisabled();
    expect(
      within(screen.getByTestId("actor-permission-conflict")).getByLabelText(
        "Both should have it",
      ),
    ).not.toBeChecked();
  });

  it("shows no conflict when the server finds none", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Lena" },
    });
    fireEvent.mouseDown(
      screen.getByLabelText("World actions they may take (optional)"),
    );
    fireEvent.click(
      screen.getByRole("option", { name: /crm.request_approval/ }),
    );
    expect(
      screen.queryByTestId("actor-permission-conflict"),
    ).not.toBeInTheDocument();
    fireEvent.click(saveButton());
    expect(sent().shareActions).toEqual([]);
  });

  it("keeps Save off, saying Checking, until the server has answered for what the form holds", () => {
    answer = undefined;
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Lena" },
    });
    expect(saveButton()).toBeDisabled();
    expect(screen.getByText("Checking…")).toBeInTheDocument();
    answer = CLEAN;
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Lena K" },
    });
    expect(saveButton()).toBeEnabled();
    expect(screen.queryByText("Checking…")).toBeNull();
  });

  it("shows a server problem under its field and keeps Save off", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya Nair" },
    });
    const wait = screen.getByLabelText("Waits, in seconds (optional)");
    answer = {
      ...CLEAN,
      problems: [
        { field: "timeout_seconds", message: "A wait is 0 seconds or more" },
      ],
    };
    fireEvent.change(wait, { target: { value: "-5" } });
    expect(screen.getByText("A wait is 0 seconds or more")).toBeInTheDocument();
    expect(screen.queryByTestId("actor-problems")).toBeNull();
    expect(saveButton()).toBeDisabled();
    answer = CLEAN;
    fireEvent.change(wait, { target: { value: "2.5" } });
    expect(saveButton()).toBeEnabled();
    fireEvent.click(saveButton());
    expect(sent().changes.timeout_seconds).toBe(2.5);
  });

  it("shows the server's id problem under Id, and the id rule in its words", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    expect(
      screen.getByText(/1 to 64 lowercase letters, digits or underscores\./),
    ).toBeInTheDocument();
    answer = {
      ...CLEAN,
      problems: [{ field: "id", message: "caller is kept by the environment" }],
    };
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "caller" },
    });
    expect(
      screen.getByText("caller is kept by the environment"),
    ).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it("shows the id the server would give until one is typed, and sends none of its own", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    expect(screen.getByLabelText("Id")).toHaveValue("");
    answer = { ...CLEAN, suggested_id: "sam_lee_2" };
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Sam Lee" },
    });
    expect(screen.getByLabelText("Id")).toHaveValue("sam_lee_2");
    fireEvent.click(saveButton());
    expect(sent()).not.toHaveProperty("id");
  });

  it("sends no id when a typed id is cleared", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ravi Kumar" },
    });
    fireEvent.click(saveButton());
    expect(sent()).not.toHaveProperty("id");
  });

  it("will not save an actor the server says is too long, and says by how much", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya Nair" },
    });
    answer = { ...CLEAN, over_by: 4200 };
    fireEvent.change(
      screen.getByLabelText("What they know (optional, one per line)"),
      { target: { value: "a long fact" } },
    );
    expect(
      screen.getByText(/4,200 characters too long to save/),
    ).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it("keeps the details a run does not read yet folded under More details", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    expect(
      screen.getByLabelText("What they know (optional, one per line)"),
    ).toBeVisible();
    expect(
      screen.getByLabelText("What they want (optional)"),
    ).not.toBeVisible();
    fireEvent.click(screen.getByText("More details"));
    expect(screen.getByLabelText("What they want (optional)")).toBeVisible();
    expect(
      screen.getByText(
        /Given to them as background whenever they speak or decide in the moment/,
      ),
    ).toBeVisible();
    expect(screen.queryByText(/not used by runs/)).toBeNull();
  });

  it("labels the role required when the chosen channel requires it", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    expect(screen.getByLabelText("Role (optional)")).toBeInTheDocument();
    choose("How they join the conversation", "Heard nearby");
    expect(screen.getByLabelText("Role")).toBeInTheDocument();
    expect(screen.getByText("a voice near the person")).toBeInTheDocument();
  });

  it("adds an actor who speaks with a channel and its loudness, and every other field too", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    addActor("On the caller's side");
    expect(
      screen.getByLabelText("World actions they may take (optional)"),
    ).toBeInTheDocument();
    choose("How they join the conversation", "Heard nearby");
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "colleague" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Jordan" },
    });
    const automatic = screen.getByRole("checkbox", {
      name: /Automatic: each call picks between 33% and 67%/,
    });
    expect(automatic).toBeChecked();
    fireEvent.click(automatic);
    expect(screen.getByRole("slider")).toHaveValue("0.5");
    expect(screen.getByText("50% of the caller's voice")).toBeInTheDocument();
    choose("Gender", "Female");
    choose("Age", "60+");
    fireEvent.click(saveButton());
    expect(sent().id).toBe("colleague");
    expect(sent().changes).toEqual({
      side: "caller",
      name: "Jordan",
      channel: "audio_mix",
      loudness_ratio: 0.5,
      gender: "female",
      age_group: "60+",
    });
  });

  it("offers loudness only on a channel the cast says has one", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    addActor("On the caller's side");
    choose("How they join the conversation", "Takes the phone");
    expect(screen.queryByText(/Automatic: each call picks/)).toBeNull();
  });

  it("changes only how an actor sounds", () => {
    actorsData = cast([
      typedActor({
        id: "colleague",
        name: "Jordan Taylor",
        side: "caller",
        channel: "audio_handoff",
        persona: { gender: "male", age_group: "32-40", occupation: "Engineer" },
      }),
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    // A stored age the server no longer offers still reads as stored.
    expect(screen.getByLabelText("Age")).toHaveTextContent("32-40");
    choose("Accent", "Indian");
    fireEvent.click(saveButton());
    expect(sent()).toMatchObject({
      op: "replace",
      actorId: "colleague",
      changes: { accent: "Indian" },
    });
  });

  it("says a change made offline is kept and saves once back online", () => {
    editState = { isPending: true, isPaused: true, error: null };
    render(<WorldActorsPanel envId="env-1" />);
    expect(
      screen.getByText(
        "No connection: your change is kept and saves once you are back online",
      ),
    ).toBeInTheDocument();
  });

  it("offers any actor, one who speaks too, to be brought in by, and sends who was picked", () => {
    actorsData = cast([
      typedActor({
        id: "colleague",
        name: "Jordan Taylor",
        channel: "audio_mix",
      }),
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.click(screen.getByText("More details"));
    choose("Brought in by (optional)", "Jordan Taylor");
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Sam" },
    });
    fireEvent.click(saveButton());
    expect(sent().changes).toMatchObject({ parent: "colleague", name: "Sam" });
  });

  it("opens a fresh form for the next actor after a save", () => {
    mutate.mockImplementation((_change, options) => options?.onSuccess?.());
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya Raman" },
    });
    fireEvent.change(screen.getByLabelText("Role (optional)"), {
      target: { value: "store manager" },
    });
    fireEvent.click(saveButton());
    addActor();
    expect(screen.getByLabelText("Role (optional)")).toHaveValue("");
  });

  it("sends only the name when only the name is edited", () => {
    actorsData = cast([
      {
        ...MANAGER,
        activation: {
          ...MANAGER.activation,
          trigger: { kind: "after_seconds", seconds: 30 },
        },
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya R." },
    });
    fireEvent.click(saveButton());
    expect(sent()).toEqual({
      op: "replace",
      actorId: "manager",
      revision: 3,
      shareActions: [],
      changes: { name: "Priya R." },
    });
  });

  it("keeps Save off for an existing actor until something really changes", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(saveButton()).toBeDisabled();
    const name = screen.getByLabelText("Name");
    fireEvent.change(name, { target: { value: "Priya R." } });
    expect(saveButton()).not.toBeDisabled();
    fireEvent.change(name, { target: { value: "Priya Raman " } });
    expect(saveButton()).toBeDisabled();
  });

  it("names how an actor comes in as the server says, and going back to it in Comes in is no change", async () => {
    actorsData = cast([
      {
        ...MANAGER,
        activation: {
          ...MANAGER.activation,
          trigger: { kind: "after_seconds", seconds: 30 },
        },
        when_label: "30 s after the start",
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    expect(
      screen.getByText(/Comes in: 30 s after the start/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(screen.getByLabelText("Comes in")).toHaveTextContent(
      "At a set time",
    );
    expect(screen.getByLabelText("Seconds after the start")).toHaveValue(30);
    choose("Comes in", "When the agent contacts them");
    expect(saveButton()).not.toBeDisabled();
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    choose("Comes in", "At a set time");
    expect(saveButton()).toBeDisabled();
  });

  it("keeps a trigger the form cannot make by the server's words, until another is picked", () => {
    actorsData = cast([
      {
        ...MANAGER,
        activation: {
          ...MANAGER.activation,
          trigger: { kind: "probability", p: 0.25 },
        },
        editable_trigger: false,
        when_label: "In about 25% of calls",
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(screen.getByLabelText("Comes in")).toHaveTextContent(
      "In about 25% of calls",
    );
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya R." },
    });
    fireEvent.click(saveButton());
    expect(sent().changes).toEqual({ name: "Priya R." });
  });

  it("shows the server's problem with how an actor comes in under Comes in", async () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    answer = {
      ...CLEAN,
      problems: [
        { field: "trigger", message: "Pick the action that brings them in" },
      ],
    };
    choose("Comes in", "When the agent calls an action");
    expect(saveButton()).toBeDisabled();
    expect(
      screen.getByText("Pick the action that brings them in"),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    answer = CLEAN;
    choose("The action", "crm.approve_discount");
    expect(saveButton()).not.toBeDisabled();
  });

  it("keeps the form open and Save off while the record match is half-typed JSON, and asks the server nothing", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya Nair" },
    });
    choose("Comes in", "Once a record exists");
    fireEvent.change(screen.getByLabelText("Matching (optional)"), {
      target: { value: '{"status": ' },
    });
    expect(asked).toBeNull();
    expect(screen.getByLabelText("Matching (optional)")).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it("sends a cap as typed, the server saying when it is not a whole number", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    const cap = screen.getByLabelText("Most turns in a call (optional)");
    answer = {
      ...CLEAN,
      problems: [
        { field: "max_turns", message: "A whole number above 0, or empty" },
      ],
    };
    fireEvent.change(cap, { target: { value: "2.7" } });
    expect(saveButton()).toBeDisabled();
    expect(
      screen.getByText("A whole number above 0, or empty"),
    ).toBeInTheDocument();
    answer = CLEAN;
    fireEvent.change(cap, { target: { value: "3" } });
    fireEvent.click(saveButton());
    expect(sent().changes.max_turns).toBe(3);
  });

  it("lists a server problem on a field not on screen above Save, so Save is never off without a reason", () => {
    // A world actor shows no lines-cap field, so that problem goes above Save.
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    answer = {
      ...CLEAN,
      problems: [{ field: "max_lines", message: "max_lines must be above 0" }],
    };
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya R." },
    });
    expect(screen.getByTestId("actor-problems")).toHaveTextContent(
      "max_lines must be above 0",
    );
    expect(saveButton()).toBeDisabled();
  });

  it("never crashes on a record match of only spaces, and sends it as no match", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya Nair" },
    });
    choose("Comes in", "Once a record exists");
    fireEvent.change(screen.getByLabelText("Matching (optional)"), {
      target: { value: "   " },
    });
    expect(screen.getByLabelText("Matching (optional)")).toBeInTheDocument();
    expect(asked.changes.trigger.where).toEqual({});
  });

  it("shows a stored value even when its select doesn't list it, and the fallback in the server's words", () => {
    actorsData = cast([
      {
        ...MANAGER,
        kind: "robot",
        parent: "colleague",
        fallback: {
          kind: "actor",
          value: "colleague",
          label: "Jordan answers instead",
        },
      },
      typedActor({ id: "colleague", name: "Jordan", channel: "audio_mix" }),
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Priya Raman" }));
    expect(screen.getByLabelText("Kind (optional)")).toHaveTextContent("robot");
    expect(screen.getByLabelText("If they do not answer")).toHaveTextContent(
      "Jordan answers instead",
    );
    expect(screen.getByLabelText("Brought in by (optional)")).toHaveTextContent(
      "Jordan",
    );
    expect(screen.getByLabelText("Comes in")).toHaveTextContent(
      "Present from the start",
    );
  });

  it("offers a fallback action's arguments only when the fallback picked is one of the actor's actions", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(screen.queryByLabelText("Taken with (JSON)")).toBeNull();
    choose("If they do not answer", "crm.approve_discount is taken for them");
    fireEvent.change(screen.getByLabelText("Taken with (JSON)"), {
      target: { value: '{"set": {"status": "declined"}}' },
    });
    fireEvent.click(saveButton());
    expect(sent().changes).toEqual({
      fallback: "crm.approve_discount",
      fallback_arguments: { set: { status: "declined" } },
    });
  });

  it("shows a loading and a failed view inside the row, with a retry", () => {
    viewState = { isLoading: true };
    const { rerender } = render(<WorldActorsPanel envId="env-1" locked />);
    fireEvent.click(screen.getByText("What they see"));
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    const retry = vi.fn();
    viewState = { isError: true, refetch: retry };
    rerender(<WorldActorsPanel envId="env-1" locked />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalled();
  });

  it.each([
    ["a gateway page with no sentence", { statusCode: 502 }, /Couldn't save/],
    ["a network failure", { message: "Network Error" }, /Couldn't save/],
    [
      "a refusal the server worded",
      { statusCode: 400, error: "actor_invalid", message: "Role is too long" },
      /Role is too long/,
    ],
  ])(
    "says what went wrong in the form and on the row: %s",
    (_, error, words) => {
      editState = { isPending: false, error };
      const { rerender } = render(<WorldActorsPanel envId="env-1" />);
      expect(screen.getByText(words)).toBeInTheDocument();
      editState = { isPending: false, error: null };
      rerender(<WorldActorsPanel envId="env-1" />);
      fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
      editState = { isPending: false, error };
      rerender(<WorldActorsPanel envId="env-1" />);
      expect(screen.getByText(words)).toBeInTheDocument();
    },
  );

  it("will not remove an actor the cast says cannot go, says why in its words, and offers no delete", () => {
    actorsData = cast([
      {
        ...MANAGER,
        id: "temp",
        name: "Temp Tina",
        removable: false,
        removal_blocked_by: [
          "Temp Tina is in 2 scenarios.",
          "Fallback Fred has Temp Tina answer instead when Fallback Fred does not; change that on Fallback Fred before removing Temp Tina.",
        ],
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Remove Temp Tina" }));
    expect(mutate).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.getByText(
        "Temp Tina is in 2 scenarios. Fallback Fred has Temp Tina answer instead when Fallback Fred does not; change that on Fallback Fred before removing Temp Tina.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps how an actor joins fixed when the cast locks it, and says why in its words", () => {
    actorsData = cast([
      {
        ...MANAGER,
        channel: "audio_mix",
        scenarios: 2,
        locked_fields: [
          {
            field: "channel",
            reason: "In 2 scenarios, so how they join stays as it is.",
          },
        ],
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(
      screen.getByText("In 2 scenarios, so how they join stays as it is."),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("How they join the conversation"),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("clears the in-use warning when a form opens", () => {
    actorsData = cast([
      { ...MANAGER, removable: false, removal_blocked_by: ["In 2 scenarios."] },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Remove / }));
    expect(screen.getByText("In 2 scenarios.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(screen.queryByText("In 2 scenarios.")).toBeNull();
  });

  it("removes an actor from the draft once confirmed, and a locked environment offers no edits", () => {
    const { unmount } = render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Remove / }));
    expect(mutate).not.toHaveBeenCalled();
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove",
      }),
    );
    expect(sent()).toEqual({
      op: "delete",
      actorId: "manager",
      revision: 3,
    });
    unmount();
    render(<WorldActorsPanel envId="env-1" locked />);
    expect(screen.queryByText("Add actor")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(
      screen.getByRole("button", { name: "What they see" }),
    ).toBeInTheDocument();
  });

  it("offers no other edit while a remove is in flight, so its answer can't be lost", () => {
    editState = {
      isPending: true,
      error: null,
      variables: { op: "delete", actorId: "manager" },
    };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByRole("button", { name: /^Edit / })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^Remove / })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add actor" })).toBeDisabled();
  });

  it("still asks when an existing value was emptied and then folded away", async () => {
    actorsData = cast([{ ...MANAGER, goal: "Approve refunds" }]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    const more = screen.getByRole("button", { name: /more details/i });
    fireEvent.click(more);
    fireEvent.change(
      screen.getByRole("textbox", { name: "What they want (optional)" }),
      { target: { value: "" } },
    );
    fireEvent.click(more);
    await waitFor(() =>
      expect(
        screen.queryByRole("textbox", { name: "What they want (optional)" }),
      ).toBeNull(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(
      screen.getByRole("dialog", { name: /Discard changes/ }),
    ).toBeInTheDocument();
  });

  it("offers the full name in a tooltip only when it is cut off", async () => {
    render(<WorldActorsPanel envId="env-1" locked />);
    const name = screen.getByText(/Priya Raman/);
    fireEvent.mouseEnter(name);
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.mouseLeave(name);
    Object.defineProperty(name, "scrollWidth", { value: 200 });
    Object.defineProperty(name, "clientWidth", { value: 100 });
    fireEvent.mouseEnter(name);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Priya Raman");
  });

  it("asks before closing a form with typed values, and keeps them if you stay", async () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    const name = screen.getByRole("textbox", { name: "Name" });
    fireEvent.change(name, { target: { value: "Sam Lee" } });
    fireEvent.keyDown(name, { key: "Escape" });
    const dialog = screen.getByRole("dialog", { name: /Discard changes/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("textbox", { name: "Name" })).toHaveValue(
      "Sam Lee",
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: /Discard changes/ })).getByRole(
        "button",
        { name: "Discard" },
      ),
    );
    expect(screen.queryByText("Save to draft")).toBeNull();
  });

  it("closes straight away with nothing typed, and when Cancel is pressed", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Save to draft")).toBeNull();
    addActor();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Sam Lee" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: /Discard changes/ }),
    ).toBeNull();
    expect(screen.queryByText("Save to draft")).toBeNull();
  });

  it("clears a failed remove's error when a form opens, and it can be dismissed", () => {
    editState = {
      isPending: false,
      error: new Error("fraud_desk depends on them"),
    };
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(resetEdit).toHaveBeenCalledTimes(1);
    addActor();
    expect(resetEdit).toHaveBeenCalledTimes(2);
  });

  it("leaves an open view open when a remove is cancelled", async () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("What they see"));
    fireEvent.click(screen.getByRole("button", { name: /^Remove / }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Cancel",
      }),
    );
    expect(await screen.findByText("Hide what they see")).toBeInTheDocument();
  });

  it("will not close or discard the form while a save is in flight", () => {
    const { rerender } = render(<WorldActorsPanel envId="env-1" />);
    addActor();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Sam Lee" },
    });
    editState = { isPending: true, error: null };
    rerender(<WorldActorsPanel envId="env-1" />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Name" }), {
      key: "Escape",
    });
    expect(
      screen.queryByRole("dialog", { name: /Discard changes/ }),
    ).toBeNull();
    expect(screen.getByText("Save to draft")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("does not ask once typed text has been deleted again", () => {
    render(<WorldActorsPanel envId="env-1" />);
    addActor();
    const name = screen.getByRole("textbox", { name: "Name" });
    fireEvent.change(name, { target: { value: "Sam Lee" } });
    fireEvent.change(name, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(
      screen.queryByRole("dialog", { name: /Discard changes/ }),
    ).toBeNull();
    expect(screen.queryByText("Save to draft")).toBeNull();
  });

  it("shows Not set for a value with nothing in it", () => {
    render(<WorldActorsPanel envId="env-1" locked />);
    fireEvent.click(screen.getByText("What they see"));
    expect(screen.getByText("Not set").parentElement).toHaveTextContent(
      "What they may agree to and keep to themselves: Not set",
    );
  });

  it("lists the policy points waiting for a decision above the cast, to accept as written or edit an actor for", () => {
    acceptMutate.mockReset();
    actorsData = cast([MANAGER], {
      unresolved: [
        {
          policy: "Discounts over 25% need the VP",
          why: "no record holds the VP's approval",
          actor: "manager",
        },
      ],
    });
    render(<WorldActorsPanel envId="env-1" />);
    const block = screen.getByTestId("needs-decision");
    expect(within(block).getByText("Needs your decision")).toBeInTheDocument();
    expect(
      within(block).getByText("Discounts over 25% need the VP"),
    ).toBeInTheDocument();
    expect(within(block).getByText(/About Priya Raman/)).toBeInTheDocument();
    fireEvent.click(
      within(block).getByRole("button", { name: "Accept as written" }),
    );
    expect(acceptMutate).toHaveBeenCalledWith(
      { policy: "Discounts over 25% need the VP", revision: 3 },
      expect.any(Object),
    );
    fireEvent.click(within(block).getByRole("button", { name: "Edit actor" }));
    expect(screen.getByLabelText("Role (optional)")).toHaveValue(
      "sales manager",
    );
  });

  it("shows no decision block when nothing is waiting", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.queryByTestId("needs-decision")).toBeNull();
  });
});
