import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios from "src/utils/axios";
import LandingPageCard from "src/components/LandingPageCard/LandingPageCard";
import WorkbenchLandingPage from "src/sections/workbench/WorkbenchLandingPage";
import ShowPromptsComponents from "src/sections/workbench/workbenchDetailView/ShowPromptsComponents";
import DatasetOptions from "src/sections/evals/AddDatasetDrawer/DatasetOptions";
import {
  OTHER_AUTH_METHODS,
  defaultAuthMethodForProvider,
} from "src/sections/agents/constants";
import * as agentDetailTabs from "src/sections/agent-playground/connected/detail/agentDetailTabs";
import FixMyAgentDrawer from "src/sections/simulate/environments/workspace/runs/detail/fixmyagent/FixMyAgentDrawer";
import { whereLine } from "src/sections/common/RunSources/WhereItRuns";
import { ENTRY_AGENT_TYPES } from "src/sections/simulate/environments/agentTypes";
import { HOSTED_PLATFORMS_BY_TYPE } from "src/sections/simulate/environments/hostedPlatforms";
import { REPO_PROVIDERS } from "src/sections/simulate/environments/repoProviders";
import {
  ENVIRONMENTS_HEADER,
  OPTIONS,
} from "src/sections/simulate/environments/environmentOptions";
import { SCENARIOS_COPY } from "src/sections/simulate/environments/workspace/scenarios/scenarios.constants";

// The rule these cases pin: a part that is not built stays visible and
// disabled, carrying one shared line, "Not available in this release", with no
// "Coming soon" text, chip or tooltip and no promise about when. A built
// feature that this deployment withholds says "Not available in this
// deployment" instead. Connector catalogs keep their own labels and are not
// covered.
const LINE = "Not available in this release";
const COMING_SOON = /coming[\s-]soon/i;

vi.mock("src/components/image", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: {
    develop: {
      runPrompt: {
        createPromptDraft: "/model-hub/prompt-templates/create-draft/",
      },
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("react-router", async (importActual) => ({
  ...(await importActual()),
  useNavigate: () => vi.fn(),
}));
vi.mock("src/hooks/useCapabilities", async (importActual) => ({
  ...(await importActual()),
  useFeatureAllowed: () => ({ allowed: false }),
}));
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useDebugAnalysis: () => ({
    analysis: {
      status: "completed",
      goals: [],
      oneOffs: [],
      summary: null,
    },
    isLoading: false,
    request: vi.fn(),
    isRequesting: false,
    requestError: null,
  }),
  useRunCalls: () => ({ tasks: [] }),
  withCallContext: (analysis) => analysis,
}));
vi.mock(
  "src/sections/simulate/environments/workspace/runs/detail/fixmyagent/DiagnosisPane",
  () => ({ default: () => <div>diagnosis-body</div> }),
);

vi.mock(
  "src/sections/simulate/environments/components/SideDrawer",
  async () => {
    const { default: types } = await import("prop-types");
    function MockSideDrawer({ open, children }) {
      return open ? <div>{children}</div> : null;
    }
    MockSideDrawer.propTypes = { open: types.bool, children: types.node };
    return { default: MockSideDrawer };
  },
);

const card = {
  title: "Unbuilt feature",
  description: "Teaser description",
  image: "/test.svg",
};

const withClient = (ui) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {ui}
  </QueryClientProvider>
);

describe("Unbuilt features are visible, disabled and say Not available in this release", () => {
  describe("landing cards", () => {
    it("keeps an unavailable landing card with a disabled action carrying the line", () => {
      render(<LandingPageCard {...card} showAction />);
      expect(screen.getByText(card.title)).toBeInTheDocument();
      expect(screen.getByText(card.description)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: LINE })).toBeDisabled();
      expect(screen.queryByRole("button", { name: "Get started" })).toBeNull();
      expect(screen.queryByText(COMING_SOON)).toBeNull();
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
  });

  describe("Prompt Management landing page (group 1)", () => {
    it("shows Generate and Improve beside Write, disabled with the line", () => {
      axios.post.mockClear();
      render(withClient(<WorkbenchLandingPage />));
      expect(
        screen.getByText("Write a prompt from scratch"),
      ).toBeInTheDocument();
      expect(
        screen.getAllByRole("button", { name: "Get started" }),
      ).toHaveLength(1);
      expect(screen.getByText("Generate a Prompt")).toBeInTheDocument();
      expect(
        screen.getByText("Improve an Existing Prompt"),
      ).toBeInTheDocument();
      const unbuilt = screen.getAllByRole("button", { name: LINE });
      expect(unbuilt).toHaveLength(2);
      unbuilt.forEach((button) => expect(button).toBeDisabled());
      fireEvent.click(screen.getByText("Generate a Prompt"));
      fireEvent.click(screen.getByText("Improve an Existing Prompt"));
      expect(axios.post).not.toHaveBeenCalled();
      expect(screen.queryByText(COMING_SOON)).toBeNull();
    });

    it("shows Generate and Improve in the Create Prompt menu, disabled with the line, and Write still works", async () => {
      axios.post.mockClear();
      const anchor = document.createElement("div");
      document.body.appendChild(anchor);
      render(
        withClient(
          <ShowPromptsComponents
            open
            onClose={vi.fn()}
            id="prompt-popper"
            ref={{ current: anchor }}
          />,
        ),
      );
      const titles = [
        "Write a prompt from scratch",
        "Generate a prompt",
        "Improve an existing prompt",
      ];
      // Write comes first, then the two unbuilt items, in that order.
      expect(
        screen.getAllByText(/prompt/i).map((el) => el.textContent),
      ).toEqual(titles);
      const [write, ...unbuilt] = titles.map((t) => screen.getByText(t));
      expect(screen.getAllByText(LINE)).toHaveLength(2);
      unbuilt.forEach((title) => {
        const row = title.closest('[aria-disabled="true"]');
        expect(row).not.toBeNull();
        expect(row.tabIndex).toBeLessThan(0);
        expect(within(row).getByText(LINE)).toBeInTheDocument();
        fireEvent.click(title);
        fireEvent.click(within(row).getByText(LINE));
      });
      expect(axios.post).not.toHaveBeenCalled();
      expect(write.closest('[aria-disabled="true"]')).toBeNull();
      expect(screen.queryByText(COMING_SOON)).toBeNull();
      fireEvent.click(write);
      await waitFor(() =>
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/prompt-templates/create-draft/",
          expect.anything(),
        ),
      );
      anchor.remove();
    });
  });

  describe("connected agent detail (group 2)", () => {
    it("keeps the built detail tabs and lists the unreleased tabs after them", () => {
      expect(agentDetailTabs.DETAIL_TABS.map((t) => t.id)).toEqual([
        "overview",
        "definition",
        "versions",
        "environments",
      ]);
      expect(agentDetailTabs.UNRELEASED_TABS).toEqual([
        "Constraints",
        "Improvements",
        "Connections",
      ]);
    });
  });

  describe("Debug failures drawer (group 3)", () => {
    it("shows the Imagine tab, whose pane is inert and carries the line", () => {
      render(<FixMyAgentDrawer open executionId="ex1" stats={{ failed: 2 }} />);
      expect(
        screen.getByRole("tab", { name: "Diagnosis" }),
      ).toBeInTheDocument();
      expect(screen.getByText("diagnosis-body")).toBeInTheDocument();
      const imagineTab = screen.getByRole("tab", { name: /^Imagine/ });
      expect(imagineTab).toHaveTextContent(LINE);
      fireEvent.click(imagineTab);
      expect(screen.queryByText("diagnosis-body")).toBeNull();
      expect(screen.getAllByText(LINE).length).toBeGreaterThanOrEqual(2);
      expect(screen.queryByRole("button")).toBeNull();
      expect(screen.queryByText(COMING_SOON)).toBeNull();
    });
  });

  describe("withheld sandbox", () => {
    it("says the sandbox is not available in this deployment", () => {
      expect(whereLine("sandbox")).toBe(
        "Future AGI sandbox: Not available in this deployment",
      );
      expect(whereLine("sandbox")).not.toMatch(/coming soon/i);
    });
  });

  describe("Build environment heroes and options (group 4)", () => {
    it("keeps Prebuilt, Web, MCP server and Build locally in the data", () => {
      const status = (id) => OPTIONS.find((o) => o.id === id)?.status;
      ["templates", "web", "mcp", "local"].forEach((id) =>
        expect(status(id)).toBe("coming_soon"),
      );
      // With the heroes back above it, the connect heading reads "Or".
      expect(ENVIRONMENTS_HEADER.connectHeading).toBe(
        "Or connect your own agent",
      );
    });
  });

  describe("hosted platform agent types and chat platforms (group 5)", () => {
    it("lists Computer use, Code and Robotics as unreleased agent types", () => {
      expect(ENTRY_AGENT_TYPES.map(({ id }) => id)).toEqual([
        "voice",
        "text",
        "computer",
        "code",
        "robotics",
      ]);
      expect(
        ENTRY_AGENT_TYPES.filter((t) => t.comingSoon).map((t) => t.label),
      ).toEqual(["Computer use", "Code", "Robotics"]);
    });

    it("lists the four unbuilt chat platforms after Retell, not selectable", () => {
      expect(HOSTED_PLATFORMS_BY_TYPE.text.map(({ id }) => id)).toEqual([
        "retell_chat",
        "openai_assistants",
        "langgraph",
        "crewai",
        "claude_agents",
      ]);
      expect(
        HOSTED_PLATFORMS_BY_TYPE.text
          .filter((p) => p.comingSoon)
          .map((p) => p.name),
      ).toEqual([
        "OpenAI Assistants",
        "LangGraph Cloud",
        "CrewAI",
        "Claude Agents",
      ]);
    });
  });

  describe("source repository hosts (group 6)", () => {
    it("lists GitLab and Bitbucket as unreleased hosts beside GitHub", () => {
      expect(REPO_PROVIDERS.map(({ id }) => id)).toEqual([
        "github",
        "gitlab",
        "bitbucket",
      ]);
      expect(
        REPO_PROVIDERS.filter((p) => p.comingSoon).map((p) => p.id),
      ).toEqual(["gitlab", "bitbucket"]);
      expect(OPTIONS.find((o) => o.id === "source").preview).toEqual([
        "GitHub",
        "GitLab",
        "Bitbucket",
      ]);
    });
  });

  describe("Add scenarios (group 8)", () => {
    it("keeps no Coming soon copy for the Add scenarios control", () => {
      expect(SCENARIOS_COPY.addLabel).toBe("Add scenarios");
      expect(SCENARIOS_COPY.addComingSoon).toBeUndefined();
      expect(Object.values(SCENARIOS_COPY).join(" ")).not.toMatch(COMING_SOON);
    });
  });

  describe("dataset options (group 11)", () => {
    it("keeps a disabled dataset option visible, inert and carrying the line", () => {
      const onClick = vi.fn();
      render(
        <DatasetOptions
          title="Unbuilt import"
          subTitle="Teaser"
          disabled
          onClick={onClick}
        />,
      );
      expect(screen.getByText("Unbuilt import")).toBeInTheDocument();
      expect(screen.getByText("Teaser")).toBeInTheDocument();
      expect(screen.getByText(LINE)).toBeInTheDocument();
      expect(
        screen.getByText("Unbuilt import").closest('[aria-disabled="true"]'),
      ).toContainElement(screen.getByText("Teaser"));
      fireEvent.click(screen.getByText("Unbuilt import"));
      fireEvent.click(screen.getByText(LINE));
      expect(onClick).not.toHaveBeenCalled();
      expect(screen.queryByText(COMING_SOON)).toBeNull();
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
  });

  describe("agent authentication (group 9)", () => {
    it("lists OAuth 2.0 and Digest auth disabled with the line, not (coming soon)", () => {
      expect(OTHER_AUTH_METHODS.map(({ value }) => value)).toEqual([
        "basicAuth",
        "bearerToken",
        "noAuth",
        "oauth2_0",
        "bigestAuth",
      ]);
      const unbuilt = OTHER_AUTH_METHODS.filter((m) => m.disabled);
      expect(unbuilt.map((m) => m.value)).toEqual(["oauth2_0", "bigestAuth"]);
      expect(unbuilt[0].label.startsWith("OAUTH 2.0")).toBe(true);
      expect(unbuilt[1].label.startsWith("DIGEST AUTH")).toBe(true);
      unbuilt.forEach((m) => {
        expect(m.label).toContain(LINE);
        expect(m.label).not.toMatch(COMING_SOON);
      });
      // Disabled choices are never preselected.
      expect(defaultAuthMethodForProvider("others")).toBe("");
    });
  });

  describe("touched source files", () => {
    // Every file this restore touches. Connector files keep their own
    // exception and are not listed.
    const TOUCHED = [
      "components/NotInRelease/notInRelease.js",
      "components/NotInRelease/NotInReleaseChip.jsx",
      "components/NotInRelease/NotInReleaseButton.jsx",
      "components/LandingPageCard/LandingPageCard.jsx",
      "sections/workbench/WorkbenchLandingPage.jsx",
      "sections/workbench/workbenchDetailView/ShowPromptsComponents.jsx",
      "sections/agent-playground/connected/detail/agentDetailTabs.js",
      "sections/agent-playground/connected/detail/AgentDetailLayout.jsx",
      "sections/agent-playground/connected/detail/AgentDefinitionTab.jsx",
      "sections/agent-playground/connected/detail/AgentVersionsTab.jsx",
      "sections/agent-playground/connected/detail/NotInReleaseCard.jsx",
      "sections/simulate/environments/workspace/runs/detail/fixmyagent/FixMyAgentDrawer.jsx",
      "sections/simulate/environments/workspace/runs/detail/fixmyagent/ImaginePane.jsx",
      "sections/simulate/environments/BuildEnvironmentTab.jsx",
      "sections/simulate/environments/cards/HeroCard.jsx",
      "sections/simulate/environments/cards/OptionCard.jsx",
      "sections/simulate/environments/environmentOptions.js",
      "sections/simulate/environments/agentTypes.js",
      "sections/simulate/environments/hostedPlatforms.js",
      "sections/simulate/environments/panels/PanelHostedPlatform.jsx",
      "sections/simulate/environments/components/ChipCard.jsx",
      "sections/simulate/environments/repoProviders.js",
      "sections/simulate/environments/workspace/runs/summary/RunsSummary.jsx",
      "sections/simulate/environments/workspace/scenarios/ScenariosStep.jsx",
      "sections/simulate/environments/workspace/scenarios/scenarios.constants.js",
      "sections/agents/constants.js",
      "sections/settings/PricingV3/PricingPage.jsx",
      "sections/evals/AddDatasetDrawer/DatasetOptions.jsx",
    ];

    it.each(TOUCHED)("%s has no Coming soon text", (file) => {
      const source = readFileSync(resolve(process.cwd(), "src", file), "utf8");
      expect(source).not.toMatch(COMING_SOON);
    });

    it("spells the line once, in the shared module", () => {
      const spelled = TOUCHED.filter((file) =>
        readFileSync(resolve(process.cwd(), "src", file), "utf8").includes(
          LINE,
        ),
      );
      expect(spelled).toEqual(["components/NotInRelease/notInRelease.js"]);
    });
  });
});
