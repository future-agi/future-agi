// Panels the reused viewers open (Annotate, Add to dataset) must paint above
// the eval-owned source host, as they do above the trace drawer in Observe.
// The real host, drawer stack and AddDataset run here, mounted in a plain page
// box as in the Usage tab's log panel; only the trace and call viewers are
// reduced to the layering code they use.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Drawer, Portal } from "@mui/material";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { render } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import AddDataset from "src/components/traceDetailDrawer/addToDataset/add-dataset";
import EvalLogSourceHost from "../EvalLogSourceHost";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws-1" }),
}));
vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return { ...actual, default: { ...actual.default, get: vi.fn() } };
});
vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
// Mirrors TraceDetailDrawerV2's layering: an in-place persistent drawer on the
// drawer stack, the Annotate temporary drawer kept off the stack (overlay),
// and the real AddDataset, which joins the stack.
function TraceViewer({ open, onClose, disablePortal }) {
  const [annotate, setAnnotate] = useState(false);
  const [dataset, setDataset] = useState(false);
  const covered = useDrawerCovered(open);
  const annotateCovered = useDrawerCovered(annotate, { overlay: true });
  return (
    <Portal disablePortal={disablePortal}>
      <Drawer
        anchor="right"
        open={open}
        onClose={onClose}
        variant="persistent"
        sx={coveredDrawerSx(covered)}
        PaperProps={{
          sx: { width: "60vw", height: "100vh", position: "fixed" },
        }}
      >
        <div data-testid="trace-body">
          <button type="button" onClick={() => setAnnotate(true)}>
            Annotate
          </button>
          <button type="button" onClick={() => setDataset(true)}>
            Move to dataset
          </button>
        </div>
        <AddDataset
          handleClose={() => setDataset(false)}
          actionToDataset={dataset}
          spanId="abcdef0123456789"
        />
        <Drawer
          anchor="right"
          open={annotate}
          onClose={() => setAnnotate(false)}
          variant="temporary"
          sx={coveredDrawerSx(annotateCovered)}
          PaperProps={{
            sx: { width: 480, height: "100vh", borderRadius: 0 },
          }}
          ModalProps={{
            BackdropProps: { style: { backgroundColor: "rgba(0,0,0,0.1)" } },
          }}
        >
          <div data-testid="annotate-panel">
            <button type="button" onClick={() => setAnnotate(false)}>
              Save annotation
            </button>
          </div>
        </Drawer>
      </Drawer>
    </Portal>
  );
}
TraceViewer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  disablePortal: PropTypes.bool,
};

// Mirrors VoiceDetailDrawerV2 in embedded mode: no drawer of its own, the
// real AddDataset for Move to dataset.
function VoiceViewer() {
  const [dataset, setDataset] = useState(false);
  return (
    <div data-testid="voice-body">
      <button type="button" onClick={() => setDataset(true)}>
        Move to dataset
      </button>
      <AddDataset
        handleClose={() => setDataset(false)}
        actionToDataset={dataset}
        selectedTraces={["22222222-2222-4222-8222-222222222222"]}
      />
    </div>
  );
}

vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: (props) => <TraceViewer {...props} />,
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: (props) => <VoiceViewer {...props} />,
}));

const LOG = "11111111-1111-4111-8111-111111111111";
const TRACE = "22222222-2222-4222-8222-222222222222";
const PROJECT = "33333333-3333-4333-8333-333333333333";
const SPAN = "abcdef0123456789";
const envelope = (result) => ({ data: { result } });
let sourceKind = "trace";
const traceData = {
  trace: { id: TRACE, project: PROJECT },
  observation_spans: [
    { observation_span: { id: SPAN, trace: TRACE, project: PROJECT } },
  ],
};
const voiceData = {
  trace_id: TRACE,
  project_id: PROJECT,
  observation_span: [{ id: SPAN, trace_id: TRACE }],
};

// Open modals portal straight into <body>, each a fixed, z-indexed layer in
// the root stacking context: the highest z-index paints on top, and among
// equal z-indexes the later one in the document does.
const zIndexOf = (el) => Number(getComputedStyle(el).zIndex) || 0;
const layers = () =>
  [...document.body.children]
    .map((el, order) => ({ el, order, z: zIndexOf(el) }))
    .filter(({ el }) => el.classList.contains("MuiModal-root"))
    .sort((a, b) => a.z - b.z || a.order - b.order);
const topLayer = () => layers().at(-1)?.el;
const sourceLayer = () =>
  screen
    .getByText("Back to evaluation")
    .closest('[role="dialog"]')
    .closest(".MuiModal-root");
// Covered drawers hide with visibility: hidden on the paper and below it.
const isHidden = (el) => {
  for (let node = el; node; node = node.parentElement) {
    if (getComputedStyle(node).visibility === "hidden") return true;
  }
  return false;
};
const chrome = () => [
  screen.getByText("Back to evaluation").closest("button"),
  screen.getByText("Close", { selector: "button" }),
];

describe("eval-log source host layering", () => {
  configure({ asyncUtilTimeout: 5000 });
  afterAll(() => configure({ asyncUtilTimeout: 1000 }));
  let user;
  let client;
  beforeEach(() => {
    user = userEvent.setup();
    axios.get.mockImplementation(async (url) => {
      // The host only ever asks for the opt-in enrichment.
      if (url === endpoints.develop.eval.getEvalLogs) {
        return envelope({
          source_navigation: {
            status: "ready",
            kind: sourceKind,
            project_id: PROJECT,
            trace_id: TRACE,
            span_id: SPAN,
            retryable: false,
          },
        });
      }
      if (url === endpoints.project.getTrace(TRACE)) return envelope(traceData);
      if (url === endpoints.project.getVoiceCallDetail)
        return envelope(voiceData);
      return envelope([]);
    });
  });
  afterEach(() => {
    cleanup();
    client?.clear();
    vi.clearAllMocks();
  });

  async function openSource(kind) {
    sourceKind = kind;
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <div data-testid="usage-log-panel">
          <EvalLogSourceHost logId={LOG} />
        </div>
      </QueryClientProvider>,
    );
    await user.click(
      await screen.findByRole("button", {
        name: kind === "trace" ? "View trace" : "View call",
      }),
    );
    await screen.findByTestId(kind === "trace" ? "trace-body" : "voice-body");
    const host = sourceLayer();
    // Guard: the stacking model below needs real computed z-indexes.
    expect(zIndexOf(host)).toBeGreaterThan(0);
    expect(topLayer()).toBe(host);
    return host;
  }
  const report = (label) =>
    // eslint-disable-next-line no-console
    console.log(
      `R2C01_LAYERS ${label} ` +
        JSON.stringify(
          layers().map(({ el, order, z }) => ({
            order,
            z,
            host: !!el.querySelector("#eval-log-source-title"),
            annotate: !!el.querySelector('[data-testid="annotate-panel"]'),
            dataset: el.textContent.includes("Move this span to a dataset"),
          })),
        ),
    );

  it("R2C-01 paints Annotate above the trace host with focus in it and Back/Close kept", async () => {
    const host = await openSource("trace");
    await user.click(screen.getByText("Annotate"));
    const panel = screen
      .getByTestId("annotate-panel")
      .closest(".MuiModal-root");
    report("trace+annotate");
    expect(topLayer()).toBe(panel);
    expect(zIndexOf(panel)).toBeGreaterThanOrEqual(zIndexOf(host));
    expect(panel.contains(document.activeElement)).toBe(true);
    for (const button of chrome()) expect(isHidden(button)).toBe(false);

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByTestId("annotate-panel")).not.toBeInTheDocument(),
    );
    expect(host.isConnected).toBe(true);
    expect(topLayer()).toBe(host);
    await user.click(
      screen.getByRole("button", { name: "Back to evaluation" }),
    );
    expect(screen.queryByTestId("trace-body")).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "View trace" }),
    ).toBeInTheDocument();
  });

  it.each(["trace", "voice_call"])(
    "R2C-01 paints Add to dataset above the %s host, and Back/Close return when it closes",
    async (kind) => {
      const host = await openSource(kind);
      await user.click(screen.getByText("Move to dataset"));
      const panel = (
        await screen.findByText("Move this span to a dataset or create one")
      ).closest(".MuiModal-root");
      report(`${kind}+dataset`);
      expect(topLayer()).toBe(panel);
      expect(zIndexOf(panel)).toBeGreaterThanOrEqual(zIndexOf(host));
      expect(panel.contains(document.activeElement)).toBe(true);
      const panelClose = panel.querySelector("button");
      expect(isHidden(panelClose)).toBe(false);

      await user.click(panelClose);
      await waitFor(() => expect(panel.isConnected).toBe(false));
      expect(host.isConnected).toBe(true);
      expect(topLayer()).toBe(host);
      for (const button of chrome()) expect(isHidden(button)).toBe(false);
      await user.click(screen.getByRole("button", { name: "Close" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(
        await screen.findByRole("button", {
          name: kind === "trace" ? "View trace" : "View call",
        }),
      ).toBeInTheDocument();
    },
  );
});
