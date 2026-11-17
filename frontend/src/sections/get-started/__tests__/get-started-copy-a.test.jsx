/* eslint-disable react/prop-types */
// Get started copy: each step has one name in the list and in the open
// pane, the brand is spelled "Future AGI", Skip uses a readable colour, and
// every step and the Explore cards keep at most one filled button.
import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  renderWithRouter,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import GetStarted from "src/pages/dashboard/get-started/GetStarted";
import GetStartedHeaderView from "../GetStartedHeaderView";
import InitialSetup from "../setup-steps/InitialSetup";
import InitialSetupOptions from "../setup-steps/InitialSetupOptions/InitialSetupOptions";
import BottomAction from "../setup-steps/InitialSetupOptions/BottomAction";
import KeySection from "../setup-steps/InitialSetupOptions/KeySection";
import CreateFirstDataset from "../setup-steps/InitialSetupOptions/CreateFirstDataset";
import CreateFirstEvaluation from "../setup-steps/InitialSetupOptions/CreateFirstEvaluation";
import RunFirstExperiment from "../setup-steps/InitialSetupOptions/RunFirstExperiment";
import SetupObsabilityInApplication from "../setup-steps/InitialSetupOptions/SetupObsabilityInApplication";
import InviteTeamMembers from "../setup-steps/InitialSetupOptions/InviteTeamMembers";
import CompleteSetup from "../setup-steps/InitialSetupOptions/CompleteSetup";
import TryOutFeatures from "../try-out-features/TryOutFeatures";
import { menuesConstant } from "../constant";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const auth = vi.hoisted(() => ({ role: "Owner" }));
const checks = vi.hoisted(() => ({ result: {} }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
// What the auth provider gives: the role it reports is the default
// workspace role, else the organization role; a level it does not know is
// empty.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "person-a", organization_role: auth.role },
    role: auth.role,
    orgLevel: null,
    wsLevel: null,
    effectiveLevel: null,
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  handleOnDocsClicked: vi.fn(),
  resetUser: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/external-content/ExternalContentFrame", () => ({
  default: () => null,
}));
// Thumbnails and card images (lazy images) are not under test.
vi.mock("src/components/image", () => ({
  default: ({ alt }) => <img alt={alt} />,
}));
// The SDK setup snippets inside the experiment and tracing steps belong to
// the project setup screens and are not under test here.
vi.mock("src/sections/project/NewProject/NewExperiment", () => ({
  default: () => null,
}));
vi.mock("src/sections/project/NewProject/NewObserve", () => ({
  default: () => null,
}));
// The walkthrough and video dialogs stay real; this records the frame a step
// hands to the dialog it opens.
const dialogFrame = vi.hoisted(() => ({ current: null }));
vi.mock("../GetStartedDemoVideo", async (importOriginal) => {
  const { default: Dialog } = await importOriginal();
  return {
    default: (props) => {
      if (props.showDemoModal) dialogFrame.current = props.videoComponent;
      return <Dialog {...props} />;
    },
  };
});

const NEW_SIGN_UP = {
  keys: false,
  dataset: false,
  evaluation: false,
  experiment: false,
  observe: false,
  invite: false,
};

const ok = (result) => Promise.resolve({ data: { status: true, result } });

beforeEach(() => {
  auth.role = "Owner";
  checks.result = { ...NEW_SIGN_UP };
  dialogFrame.current = null;
  window.sessionStorage.clear();
  http.get.mockReset();
  http.post.mockReset();
  http.get.mockImplementation((url) => {
    if (url === endpoints.getStarted.getTabs) return ok({ ...checks.result });
    if (url === endpoints.settings.v2.deploymentInfo) return ok({ mode: "ee" });
    if (url === endpoints.auth.create_org)
      return ok({ org_name: "Example org", results: [] });
    if (url === endpoints.settings.teams.getMemberList)
      return ok({ results: [] });
    if (url === endpoints.settings.customModal.getCustomModal)
      return Promise.resolve({ data: { results: [] } });
    return ok({});
  });
});

const light = createTheme({ palette: palette("light") });
const dark = createTheme({ palette: palette("dark") });

// The app's theme with its component styles, as theme/index.jsx builds it.
const appTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
};

const renderUi = (ui, options = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    { route: "/dashboard/get-started", ...options },
  );
};

// Lets the requests a step makes answer and render before asserting.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });

// Each step's own line, which this copy pass leaves as it is.
const STEP_LINE = {
  addKeys:
    "Add a key for each model provider you use so your prompts and experiments can call its models.",
  createFirstDataset:
    "Create a dataset to hold the examples you test your prompts and models against.",
  createFirstEvaluation:
    "Create an evaluation to score your model outputs automatically instead of checking each one by hand.",
  RunFirstExperiment:
    "Run an experiment on a dataset to compare prompts and models side by side and keep the one that works best.",
  SetupObsabilityInApplication:
    "Send traces from your application so you can follow each request and spot problems in production.",
  inviteTeamMembers:
    "Invite teammates to your organization so they can sign in and work with you.",
};

// The open pane's title: the line above the step's own line.
const paneTitle = (label) =>
  screen.getByText(STEP_LINE[label]).parentElement.firstElementChild
    .textContent;

const FILLED = ".MuiButton-contained";

describe("Get started page: tab title, heading and brand", () => {
  it("names the browser tab 'Get started · Future AGI'", async () => {
    renderUi(
      <HelmetProvider>
        <GetStarted />
      </HelmetProvider>,
    );

    await waitFor(() =>
      expect(document.title).toBe("Get started · Future AGI"),
    );
  });

  it("a new sign-up's page reads 'Get started' and spells the brand 'Future AGI'", async () => {
    renderUi(
      <HelmetProvider>
        <GetStarted />
      </HelmetProvider>,
    );

    // The steps render once the checks answer; the cards below them load
    // after that.
    await screen.findByText(STEP_LINE.addKeys);
    await screen.findAllByText(/^(Try out our features|Explore)$/);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      /^Get started$/,
    );
    expect(document.body.textContent).not.toMatch(/FutureAGI/);
  });

  it("the subtitle says the steps can be done in any order, then links the docs", () => {
    renderWithRouter(<GetStartedHeaderView />, {
      route: "/dashboard/get-started",
    });

    const link = screen.getByRole("link", { name: "View docs" });
    expect(link).toHaveAttribute("href", "https://docs.futureagi.com");
    expect(link.parentElement).toHaveTextContent(
      /^Set up your workspace\. Do the steps in any order\. View docs$/,
    );
  });

  it("the invite form names the brand 'Future AGI'", async () => {
    renderUi(<InviteTeamMembers />);

    expect(
      await screen.findByText("Invite people to collaborate in Future AGI"),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/FutureAGI/);
  });
});

describe("Get started steps: one name in the list and the open pane", () => {
  it("names the steps in sentence case", () => {
    expect(menuesConstant.menusList.map((item) => item.title)).toEqual([
      "Add provider keys",
      "Create your first dataset",
      "Create your first evaluation",
      "Run your first experiment",
      "Send your first trace",
      "Invite team members",
    ]);
  });

  it.each(menuesConstant.menusList.map((item, index) => [item.label, index]))(
    "%s: the open pane's title is the step's title in the list",
    async (label, index) => {
      renderUi(
        <>
          <div data-testid="step-list">
            <InitialSetup
              currentLabel={label}
              setCurrentLabel={vi.fn()}
              data={{}}
            />
          </div>
          <InitialSetupOptions
            currentLabel={label}
            setCurrentLabel={vi.fn()}
            data={{}}
          />
        </>,
      );
      await settle();

      const listTitles = within(screen.getByTestId("step-list"))
        .getAllByRole("button")
        .map((button) => button.textContent.trim());
      expect(listTitles).toHaveLength(menuesConstant.menusList.length);
      expect(paneTitle(label)).toBe(listTitles[index]);
    },
  );

  it("the step list is headed 'Setup steps'", () => {
    renderWithRouter(
      <InitialSetup
        currentLabel="addKeys"
        setCurrentLabel={vi.fn()}
        data={{}}
      />,
      { route: "/dashboard/get-started" },
    );

    expect(screen.getByText("Setup steps")).toBeInTheDocument();
    expect(screen.queryByText("Initial Setup")).not.toBeInTheDocument();
  });
});

describe("Get started: at most one filled button per step", () => {
  it.each([
    ["createFirstDataset"],
    ["createFirstEvaluation"],
    ["RunFirstExperiment"],
    ["SetupObsabilityInApplication"],
    ["inviteTeamMembers"],
  ])("%s has at most one filled button", async (label) => {
    const { container } = renderUi(
      <InitialSetupOptions
        currentLabel={label}
        setCurrentLabel={vi.fn()}
        data={{}}
      />,
    );
    await settle();
    if (label === "inviteTeamMembers") {
      await screen.findByRole("button", { name: "Continue" });
    }

    expect(container.querySelectorAll(FILLED).length).toBeLessThanOrEqual(1);
  });

  it("the invite step keeps the form's 'Continue' filled and its bar's 'See summary' outlined", async () => {
    const { container } = renderUi(
      <InitialSetupOptions
        currentLabel="inviteTeamMembers"
        setCurrentLabel={vi.fn()}
        data={{}}
      />,
    );

    expect(await screen.findByRole("button", { name: "Continue" })).toHaveClass(
      "MuiButton-contained",
    );
    const summary = screen.getByRole("button", { name: "See summary" });
    expect(summary).toHaveClass("MuiButton-outlined");
    expect(summary).not.toHaveClass("MuiButton-contained");
    expect(container.querySelectorAll(FILLED)).toHaveLength(1);
  });

  it.each([
    ["RunFirstExperiment", RunFirstExperiment],
    ["SetupObsabilityInApplication", SetupObsabilityInApplication],
  ])("%s: the video card's 'Watch video' is outlined", async (_label, Step) => {
    renderUi(<Step setCurrentLabel={vi.fn()} />);
    await settle();

    const watch = screen.getByRole("button", { name: "Watch video" });
    expect(watch).toHaveClass("MuiButton-outlined");
    expect(watch).not.toHaveClass("MuiButton-contained");
  });
});

describe("Get started: Skip is readable", () => {
  it.each([
    ["addKeys", KeySection],
    ["createFirstDataset", CreateFirstDataset],
    ["createFirstEvaluation", CreateFirstEvaluation],
    ["RunFirstExperiment", RunFirstExperiment],
    ["SetupObsabilityInApplication", SetupObsabilityInApplication],
  ])(
    "%s: Skip uses the secondary text colour, not the disabled one",
    async (_label, Step) => {
      renderUi(<Step setCurrentLabel={vi.fn()} />);
      await settle();

      const skip = screen.getByRole("button", { name: "Skip" });
      expect(skip).not.toHaveStyle({ color: light.palette.text.disabled });
      expect(skip).toHaveStyle({ color: light.palette.text.secondary });
    },
  );
});

describe("Get started: the steps' own copy", () => {
  it("Add provider keys: the pane uses the list's name", async () => {
    renderUi(<KeySection setCurrentLabel={vi.fn()} />);
    await settle();

    expect(paneTitle("addKeys")).toBe("Add provider keys");
    expect(screen.queryByText("Add Keys")).not.toBeInTheDocument();
  });

  it("dataset step: what you can do, plain walkthrough names and 'Go to Datasets'", async () => {
    renderUi(<CreateFirstDataset setCurrentLabel={vi.fn()} />);
    await settle();

    const heading = screen.getByText("What you can do");
    expect(
      [...heading.parentElement.querySelectorAll("li")].map(
        (item) => item.textContent,
      ),
    ).toEqual([
      "Add data from a CSV file, Hugging Face, the SDK or synthetic generation.",
      "Add columns that run a prompt or an evaluation on every row.",
      "Run experiments on a dataset to compare prompts and models.",
    ]);
    for (const name of [
      "Add a column that runs a prompt",
      "Create a synthetic dataset",
      "Run an experiment",
      "Annotate rows",
      "Optimize a prompt",
    ]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Go to Datasets" })).toHaveClass(
      "MuiButton-contained",
    );
    // The step's title is not repeated as the list's heading.
    expect(screen.getAllByText("Create your first dataset")).toHaveLength(1);
  });

  it("evaluation step: what you can do, plain walkthrough names and 'Go to Evals'", async () => {
    renderUi(<CreateFirstEvaluation setCurrentLabel={vi.fn()} />);
    await settle();

    const heading = screen.getByText("What you can do");
    expect(
      [...heading.parentElement.querySelectorAll("li")].map(
        (item) => item.textContent,
      ),
    ).toEqual([
      "Set up an evaluation for a prompt or model.",
      "Compare evaluation results to choose an approach.",
      "Save evaluations to reuse them.",
    ]);
    for (const name of ["Evaluate a RAG app", "Evaluate images"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Go to Evals" })).toHaveClass(
      "MuiButton-contained",
    );
    expect(
      screen.queryByText("Set Up Your First Evaluation"),
    ).not.toBeInTheDocument();
  });

  it("tracing step: 'Send your first trace', a named video, 'Watch video', 'View docs', and a 'Video' dialog with 'Close'", async () => {
    const user = userEvent.setup();
    renderUi(<SetupObsabilityInApplication setCurrentLabel={vi.fn()} />);
    await settle();

    expect(paneTitle("SetupObsabilityInApplication")).toBe(
      "Send your first trace",
    );
    expect(
      screen.getByText("What tracing is and how to set it up"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Monitor and analyze/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View docs" })).toHaveAttribute(
      "href",
      "https://docs.futureagi.com/docs/observe",
    );

    await user.click(screen.getByRole("button", { name: "Watch video" }));
    const dialog = await screen.findByRole("presentation");
    expect(within(dialog).getByText("Video")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "View docs" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("Demo")).not.toBeInTheDocument();
    expect(
      within(dialog).queryByText("Start experimenting"),
    ).not.toBeInTheDocument();
  });

  it("experiment step: a named video and a 'Video' dialog with 'Close'", async () => {
    const user = userEvent.setup();
    renderUi(<RunFirstExperiment setCurrentLabel={vi.fn()} />);
    await settle();

    expect(screen.getByText("How to run an experiment")).toBeInTheDocument();
    expect(screen.queryByText(/Start testing models/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Watch video" }));
    const dialog = await screen.findByRole("presentation");
    expect(within(dialog).getByText("Video")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Close" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText("Start experimenting"),
    ).not.toBeInTheDocument();
  });
});

describe("Get started: each step's walkthrough frame keeps its own ratio", () => {
  it("the dataset step's walkthroughs open in a 45.000% frame", async () => {
    const user = userEvent.setup();
    renderUi(<CreateFirstDataset setCurrentLabel={vi.fn()} />);
    await settle();

    await user.click(
      screen.getByRole("button", { name: "Add a column that runs a prompt" }),
    );
    await screen.findByRole("presentation");
    expect(dialogFrame.current.props.style.paddingBottom).toBe(
      "calc(45.000% + 41px)",
    );
  });

  it("guard: the evaluation step's walkthroughs open in a 47.400% frame", async () => {
    const user = userEvent.setup();
    renderUi(<CreateFirstEvaluation setCurrentLabel={vi.fn()} />);
    await settle();

    await user.click(
      screen.getByRole("button", { name: "Evaluate a RAG app" }),
    );
    await screen.findByRole("presentation");
    expect(dialogFrame.current.props.style.paddingBottom).toBe(
      "calc(47.400% + 41px)",
    );
  });
});

describe("Get started: the step bar", () => {
  it.each([
    ["SetupObsabilityInApplication", "Go to Tracing"],
    ["RunFirstExperiment", "Go to Datasets"],
    ["inviteTeamMembers", "See summary"],
    ["addKeys", "Next"],
  ])("%s: the bar's button reads '%s'", async (label, name) => {
    renderUi(<BottomAction currentLabel={label} setCurrentLabel={vi.fn()} />);
    await settle();

    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  });

  it("with a dataset, the experiment step's bar reads 'Go to Experiments'", async () => {
    http.get.mockImplementation((url) =>
      url === endpoints.develop.w9.datasetsQuery
        ? ok({
            datasets: [{ id: "ds-1", name: "Support tickets" }],
            total_count: 1,
            total_pages: 1,
          })
        : ok({}),
    );
    renderUi(
      <BottomAction
        currentLabel="RunFirstExperiment"
        setCurrentLabel={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("button", { name: "Go to Experiments" }),
    ).toBeInTheDocument();
  });

  it("the bar's background is the paper surface in the dark theme", async () => {
    const { container } = renderUi(
      <BottomAction currentLabel="addKeys" setCurrentLabel={vi.fn()} />,
      { theme: dark },
    );
    await settle();

    expect(container.firstElementChild).toHaveStyle({
      backgroundColor: dark.palette.background.paper,
    });
  });
});

describe("Get started: Explore", () => {
  const CARDS = [
    {
      title: "Datasets",
      description:
        "Build test data, then run prompts, evals and experiments against it.",
      button: "Go to Datasets",
      docs: "https://docs.futureagi.com/docs/dataset",
    },
    {
      title: "Experiments",
      description: "Compare prompts and models side by side on a dataset.",
      button: "Go to Datasets",
      docs: "https://docs.futureagi.com/docs/dataset/features/experiments",
    },
    {
      title: "Evals",
      description:
        "Create, test and reuse evals that score responses for accuracy, safety, tone and more.",
      button: "Go to Evals",
      docs: "https://docs.futureagi.com/docs/evaluation",
    },
  ];

  it("is headed 'Explore' and has no filled button", async () => {
    const { container } = renderUi(
      <TryOutFeatures onAddProviderKey={vi.fn()} />,
    );
    await settle();

    expect(screen.getByText("Explore")).toBeInTheDocument();
    expect(screen.queryByText("Try out our features")).not.toBeInTheDocument();
    expect(container.querySelectorAll(FILLED)).toHaveLength(0);
    expect(
      screen.getAllByRole("img").map((image) => image.getAttribute("alt")),
    ).toEqual(CARDS.map((card) => card.title));
  });

  it.each(CARDS.map((card) => [card.title, card]))(
    "the %s card: its title, line, 'View docs' and an outlined button",
    async (title, card) => {
      renderUi(<TryOutFeatures onAddProviderKey={vi.fn()} />);
      await settle();

      const root = screen.getByRole("img", { name: title }).parentElement;
      const heading = within(root).getByText(title);
      expect(heading).toHaveClass("MuiTypography-title_xs");
      expect(heading).toHaveStyle({ color: light.palette.text.primary });
      const line = within(root).getByText(card.description);
      expect(line).toHaveClass("MuiTypography-body_sm");
      expect(line).toHaveStyle({ color: light.palette.text.secondary });
      expect(
        within(root).getByRole("link", { name: "View docs" }),
      ).toHaveAttribute("href", card.docs);
      expect(within(root).queryByText("Learn more")).not.toBeInTheDocument();
      const button = within(root).getByRole("button", { name: card.button });
      expect(button).toHaveClass("MuiButton-outlined");
      expect(button).not.toHaveClass("MuiButton-contained");
    },
  );

  it.each(CARDS.map((card) => [card.title, card]))(
    "the %s card's button label takes the outlined button's colour, not the primary colour",
    async (title, card) => {
      const theme = appTheme("light");
      renderUi(<TryOutFeatures onAddProviderKey={vi.fn()} />, { theme });
      await settle();

      const root = screen.getByRole("img", { name: title }).parentElement;
      const button = within(root).getByRole("button", { name: card.button });
      const label = within(button).getByText(card.button);
      expect(label).not.toHaveStyle({ color: theme.palette.primary.main });
    },
  );
});

describe("Get started: progress in the step list", () => {
  const TWO_DONE = { ...NEW_SIGN_UP, keys: true, dataset: true };

  const renderList = (data, currentLabel = "createFirstDataset") =>
    renderWithRouter(
      <InitialSetup
        currentLabel={currentLabel}
        setCurrentLabel={vi.fn()}
        data={data}
      />,
      { route: "/dashboard/get-started" },
    );

  it.each([
    ["Owner", "2 of 6 done"],
    ["Member", "2 of 5 done"],
  ])("for the %s role, counts the steps listed: '%s'", (role, progress) => {
    auth.role = role;
    renderList(TWO_DONE);

    expect(screen.getByText(progress)).toBeInTheDocument();
    expect(
      screen.queryByText("Pick any step to open it."),
    ).not.toBeInTheDocument();
  });

  it("a Member's count leaves out the invite step they are not shown, even when it is done", () => {
    auth.role = "Member";
    renderList({ ...TWO_DONE, invite: true });

    expect(screen.getByText("2 of 5 done")).toBeInTheDocument();
  });

  it("a new sign-up starts at '0 of 6 done'", () => {
    renderList(NEW_SIGN_UP, "addKeys");

    expect(screen.getByText("0 of 6 done")).toBeInTheDocument();
  });

  it("marks each finished step 'Done', the open one included", () => {
    renderList(TWO_DONE);

    const steps = screen.getAllByRole("button");
    expect(steps).toHaveLength(6);
    // The open step, "Create your first dataset", is done as well.
    for (const step of steps.slice(0, 2)) {
      const done = within(step).getByText("Done");
      expect(done).toHaveClass("MuiTypography-body_sm");
      expect(done).toHaveStyle({ color: light.palette.text.secondary });
    }
    for (const step of steps.slice(2)) {
      expect(within(step).queryByText("Done")).not.toBeInTheDocument();
    }
    expect(
      screen.getByRole("button", { name: "Add provider keys Done" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create your first dataset Done" }),
    ).toBeInTheDocument();
  });
});

describe("Get started: the summary reports the real state", () => {
  const FOUR_DONE = {
    keys: true,
    dataset: true,
    evaluation: true,
    experiment: true,
    observe: false,
    invite: false,
  };
  const ALL_DONE = { ...FOUR_DONE, observe: true, invite: true };

  it("with four of six steps done, says so and points to the steps left", () => {
    const { container } = renderWithRouter(<CompleteSetup data={FOUR_DONE} />, {
      route: "/dashboard/get-started?tab=completed",
    });

    expect(screen.getByText("4 of 6 steps done")).toHaveClass(
      "MuiTypography-title_sm",
    );
    expect(
      screen.getByText("Open a step on the left to finish it."),
    ).toHaveClass("MuiTypography-body_md");
    expect(container.textContent).not.toMatch(/all set up/i);
    expect(container.textContent).not.toMatch(/Setup is complete/);
    expect(container.querySelector(".component-iconify")).toBeNull();
  });

  it("with all six done, says setup is complete, with the check icon", () => {
    const { container } = renderWithRouter(<CompleteSetup data={ALL_DONE} />, {
      route: "/dashboard/get-started?tab=completed",
    });

    expect(screen.getByText("Setup is complete")).toHaveClass(
      "MuiTypography-title_sm",
    );
    expect(
      screen.getByText("Open any module from the menu on the left."),
    ).toHaveClass("MuiTypography-body_md");
    expect(container.textContent).not.toMatch(/all set up/i);
    expect(container.querySelector(".component-iconify")).not.toBeNull();
  });

  it("the summary view reads the checks the page already has", async () => {
    renderUi(
      <InitialSetupOptions
        currentLabel="completed"
        setCurrentLabel={vi.fn()}
        data={FOUR_DONE}
      />,
    );
    await settle();

    expect(screen.getByText("4 of 6 steps done")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/all set up/i);
  });
});

describe("Get started: the summary counts the steps listed for the reader", () => {
  const FIVE_LISTED_DONE = {
    keys: true,
    dataset: true,
    evaluation: true,
    experiment: true,
    observe: true,
    invite: false,
  };
  const TWO_DONE = { ...NEW_SIGN_UP, keys: true, dataset: true };
  // Roles that cannot invite anywhere: the invite step is listed for them
  // but not counted. An Admin can invite, so the invite step counts for them
  // as for an Owner.
  const OTHER_ROLES = [["Member"], ["Viewer"]];

  // The step list and the summary next to it, as the page shows them.
  const renderListAndSummary = (data) =>
    renderWithRouter(
      <>
        <div data-testid="step-list">
          <InitialSetup
            currentLabel="completed"
            setCurrentLabel={vi.fn()}
            data={data}
          />
        </div>
        <div data-testid="summary">
          <InitialSetupOptions
            currentLabel="completed"
            setCurrentLabel={vi.fn()}
            data={data}
          />
        </div>
      </>,
      { route: "/dashboard/get-started?tab=completed" },
    );
  const list = () => within(screen.getByTestId("step-list"));
  const summary = () => within(screen.getByTestId("summary"));
  const checkIcon = () =>
    screen.getByTestId("summary").querySelector(".component-iconify");

  it.each(OTHER_ROLES)(
    "for the %s role, with the five listed steps done, the list and the summary both read complete",
    (role) => {
      auth.role = role;
      renderListAndSummary(FIVE_LISTED_DONE);

      expect(summary().queryByText(/steps done$/)).not.toBeInTheDocument();
      expect(list().getByText("5 of 5 done")).toBeInTheDocument();
      expect(summary().getByText("Setup is complete")).toHaveClass(
        "MuiTypography-title_sm",
      );
      expect(
        summary().getByText("Open any module from the menu on the left."),
      ).toBeInTheDocument();
      expect(checkIcon()).not.toBeNull();
    },
  );

  it.each(OTHER_ROLES)(
    "for the %s role, with two listed steps done, the summary counts the same steps as the list",
    (role) => {
      auth.role = role;
      renderListAndSummary(TWO_DONE);

      const listed = list().getByText(/^\d+ of \d+ done$/).textContent;
      const counted = summary().getByText(/steps done$/).textContent;
      expect(counted).toBe("2 of 5 steps done");
      expect(listed).toBe("2 of 5 done");
      expect(counted.split(" ")[2]).toBe(listed.split(" ")[2]);
      expect(
        summary().getByText("Open a step on the left to finish it."),
      ).toBeInTheDocument();
      expect(checkIcon()).toBeNull();
    },
  );

  it("guard: an Owner with five of six steps done reads '5 of 6 steps done'", () => {
    renderListAndSummary(FIVE_LISTED_DONE);

    expect(list().getByText("5 of 6 done")).toBeInTheDocument();
    expect(summary().getByText("5 of 6 steps done")).toBeInTheDocument();
    expect(summary().queryByText("Setup is complete")).not.toBeInTheDocument();
    expect(checkIcon()).toBeNull();
  });

  it("guard: an Owner with six of six steps done reads 'Setup is complete'", () => {
    renderListAndSummary({ ...FIVE_LISTED_DONE, invite: true });

    expect(list().getByText("6 of 6 done")).toBeInTheDocument();
    expect(summary().getByText("Setup is complete")).toBeInTheDocument();
    expect(summary().queryByText(/steps done$/)).not.toBeInTheDocument();
    expect(checkIcon()).not.toBeNull();
  });

  it("guard: with no step done, an Owner's summary reads '0 of 6 steps done' with no check icon", () => {
    const { container } = renderWithRouter(
      <CompleteSetup data={NEW_SIGN_UP} />,
      { route: "/dashboard/get-started?tab=completed" },
    );

    expect(screen.getByText("0 of 6 steps done")).toHaveClass(
      "MuiTypography-title_sm",
    );
    expect(
      screen.getByText("Open a step on the left to finish it."),
    ).toHaveClass("MuiTypography-body_md");
    expect(container.textContent).not.toMatch(/Setup is complete/);
    expect(container.querySelector(".component-iconify")).toBeNull();
  });

  it.each(OTHER_ROLES)(
    "for the %s role, with no step done, the summary reads '0 of 5 steps done'",
    (role) => {
      auth.role = role;
      const { container } = renderWithRouter(
        <CompleteSetup data={NEW_SIGN_UP} />,
        { route: "/dashboard/get-started?tab=completed" },
      );

      expect(screen.getByText(/steps done$/)).toHaveTextContent(
        /^0 of 5 steps done$/,
      );
      expect(
        screen.getByText("Open a step on the left to finish it."),
      ).toBeInTheDocument();
      expect(container.querySelector(".component-iconify")).toBeNull();
    },
  );
});
