import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  cleanup,
} from "src/utils/test-utils";
import { fireEvent } from "@testing-library/react";
import SpanDetailPane from "../SpanDetailPane";
import {
  readRecordingPreview,
  resolveAttachments,
} from "../../attachments/client";
import SequentialPlayback from "../../attachments/SequentialPlayback";
import {
  spanImage,
  standaloneManifest,
  projectId,
  parent,
  readyGrant,
} from "../../attachments/__tests__/fixtures";
vi.mock("../../attachments/client", () => ({
  readRecordingPreview: vi.fn(),
  resolveAttachments: vi.fn(),
}));
vi.mock("../../attachments/mediaSession", async (imp) => ({
  ...(await imp()),
  captureMediaContext: () => ({ generation: 1 }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
const item = (i, purpose = "conversation_recording") =>
  spanImage(i, {
    reference: { modality: "audio", mime_type: "audio/wav", purpose },
  });
const owner = (id, items, children = [], attrs = {}) => ({
  observation_span: {
    id,
    trace: parent.trace_id,
    name: "agent_session",
    span_attributes: attrs,
    attachments: items,
  },
  children,
});
const recording = item(1),
  clip = item(2, "turn_recording"),
  call = owner(parent.span_id, [recording], [], {
    "fi.conversation.transcript": [
      { role: "user", text: "Which order?", start_ms: 100 },
    ],
  });
const root = owner("4000000000000001", [], [call]);
const qc = new QueryClient();
function pane(props = {}) {
  return (
    <QueryClientProvider client={qc}>
      <SpanDetailPane
        projectId={projectId}
        entry={root}
        isRootSpan
        allSpans={[]}
        conversationTree={[root]}
        {...props}
      />
    </QueryClientProvider>
  );
}
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  readRecordingPreview.mockImplementation(async (_, p, i) => ({
    parent: p,
    state: "ready",
    occurrence_id: i.reference.occurrence_id,
    manifest: {
      ...standaloneManifest,
      recording_id: i.reference.occurrence_id,
      segments: [
        {
          start_ms: 0,
          duration_ms: 500,
          index: 0,
          occurrence_id: i.reference.occurrence_id,
        },
      ],
      duration_ms: 500,
    },
  }));
  resolveAttachments.mockImplementation(async (_, requests) =>
    requests.map((i) =>
      i.representation === "peaks"
        ? { occurrence_id: i.occurrence_id, status: "not_found" }
        : readyGrant(i.occurrence_id),
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});
it("lazily reads the child owner from an unfiltered tree and seeks the same paused engine", async () => {
  const seek = vi.spyOn(SequentialPlayback.prototype, "setTime");
  const user = userEvent.setup();
  const { rerender } = render(pane());
  expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(readRecordingPreview).not.toHaveBeenCalled();
  await user.click(screen.getByRole("tab", { name: "Conversation" }));
  const panel = screen.getByRole("tabpanel", { name: "Conversation" });
  await screen.findByRole("button", { name: "Play recording" });
  expect(readRecordingPreview.mock.calls[0].slice(0, 2)).toEqual([
    projectId,
    parent,
  ]);
  const slider = screen.getByRole("slider", { name: "Recording position" });
  const element = panel.querySelector("audio");
  await user.click(
    screen.getByRole("button", { name: /Seek to .*Which order/ }),
  );
  expect(seek).toHaveBeenCalledWith(0.1);
  expect(slider).toHaveValue("0.1");
  expect(element).not.toHaveAttribute("src");
  expect(panel.querySelector("audio")).toBe(element);
  expect(screen.queryByText("Mixed recording")).toBeNull();
  rerender(pane({ conversationTree: [] }));
  expect(screen.queryByRole("tab", { name: "Conversation" })).toBeNull();
  expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(readRecordingPreview.mock.calls[0][3].aborted).toBe(true);
});
it.each([
  { readOnly: true },
  { drawerOpen: false },
  { conversationTree: undefined },
  { projectId: "bad" },
])("does not expose the new tab in an ineligible drawer", (props) => {
  render(pane(props));
  expect(screen.queryByRole("tab", { name: "Conversation" })).toBeNull();
  expect(readRecordingPreview).not.toHaveBeenCalled();
  expect(resolveAttachments).not.toHaveBeenCalled();
});
it("multiple recordings wait for explicit selection and never use the root as resolver parent", async () => {
  const other = owner("4000000000000007", [item(6)]),
    tree = owner(root.observation_span.id, [], [call, other]);
  render(pane({ entry: tree, conversationTree: [tree] }));
  fireEvent.click(screen.getByRole("tab", { name: "Conversation" }));
  expect(readRecordingPreview).not.toHaveBeenCalled();
  const selector = screen.getByRole("combobox");
  fireEvent.change(selector, { target: { value: selector.options[2].value } });
  await screen.findByRole("button", { name: "Play recording" });
  expect(readRecordingPreview.mock.calls[0][1].span_id).toBe(
    other.observation_span.id,
  );
});
it.each(["pending", "waiting", "failed"])(
  "timeline remains visible with no transport while %s",
  async (state) => {
    readRecordingPreview.mockResolvedValue({ state });
    render(pane());
    fireEvent.click(screen.getByRole("tab", { name: "Conversation" }));
    await screen.findByText(
      state === "failed" ? "Recording unavailable" : "Recording is processing",
    );
    expect(screen.getByText("Which order?")).toBeVisible();
    expect(resolveAttachments).not.toHaveBeenCalled();
  },
);
it("renders one verified clip beside the explicit attribute, none in the attachment list, and stops on collapse", async () => {
  const attr = {
    "turn.audio": `fi-attachment:${clip.reference.occurrence_id}`,
    "fi.attachment.refs": JSON.stringify([clip.reference]),
  };
  const child = owner(parent.span_id, [clip], [], attr);
  const user = userEvent.setup();
  render(pane({ entry: child, conversationTree: [child] }));
  expect(screen.queryByRole("tab", { name: "Conversation" })).toBeNull();
  expect(screen.queryByLabelText("Span attachments")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Play clip: turn.audio" }),
  );
  const el = screen.getByLabelText("Clip: turn.audio");
  await waitFor(() => expect(el).toHaveAttribute("src"));
  await user.click(screen.getByRole("button", { name: "Toggle attributes" }));
  await waitFor(() => expect(el).not.toBeInTheDocument());
  expect(el).not.toHaveAttribute("src");
  expect(resolveAttachments).toHaveBeenCalledTimes(1);
});
