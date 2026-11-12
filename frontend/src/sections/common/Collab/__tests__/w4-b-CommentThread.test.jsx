// W4-1: the thread UI: anchors, reactions, the actions the
// server allows, resolve folding, and posting from the composer.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const post = vi.fn().mockResolvedValue({});
const patch = vi.fn().mockResolvedValue({});
const del = vi.fn().mockResolvedValue({});
let data;
vi.mock("src/api/develop/row-collab", () => ({
  collabError: (e, f) => f,
  useRowComments: () => ({ data, isLoading: false, isError: false }),
  usePostComment: () => ({ mutateAsync: post, isPending: false }),
  usePatchComment: () => ({ mutateAsync: patch }),
  useDeleteComment: () => ({ mutateAsync: del }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useOrgMembersInfinite: () => ({
    data: [
      { id: "u-meera", name: "Meera Iyer", email: "meera.iyer@acme.ai" },
      { id: "u-alice", name: "Alice Park", email: "alice.park@acme.ai" },
    ],
    isLoading: false,
  }),
}));
vi.mock("src/sections/common/ux/toast", () => ({
  notify: vi.fn(),
  default: vi.fn(),
}));

const { default: CommentThreads } = await import("../CommentThread");

const comment = (over) => ({
  id: "c1",
  row_id: "r1",
  parent_id: null,
  author: { id: "u-asha", name: "Asha Rao" },
  body: "@[Maya Chen](user:u-me) check the **tool-call** eval",
  anchor: { kind: "field", column_id: "col-3", column_name: "expected_reply" },
  reactions: { "👍": ["u-ravi"] },
  resolved: false,
  created_at: "2026-10-22T10:00:00Z",
  deleted: false,
  ...over,
});
const base = (over = {}) => ({
  threads: [
    { root: comment(), replies: [], can_resolve: true, mention_ids: ["u-me"] },
  ],
  open: 1,
  limits: { body_max: 2000, warn_from: 1800, reactions: ["👍", "✅", "👀"] },
  me: {
    id: "u-me",
    name: "Maya Chen",
    is_admin: false,
    perms: { view: true, edit: false },
  },
  access: { "u-meera": true, "u-alice": false },
  ...over,
});

const renderIt = (props = {}) =>
  render(
    <MemoryRouter>
      <CommentThreads
        datasetId="ds"
        rowId="r1"
        status="all"
        anchorOptions={[
          { key: "row", label: "Whole row", anchor: { kind: "row" } },
        ]}
        {...props}
      />
    </MemoryRouter>,
  );

beforeEach(() => {
  data = base();
  post.mockClear();
  patch.mockClear();
  del.mockClear();
});
afterEach(cleanup);

describe("CommentThreads", () => {
  it("shows the anchor, the body with the mention, reactions and allowed actions", () => {
    renderIt();
    expect(screen.getByTestId("comment-anchor").textContent).toBe(
      "on expected_reply",
    );
    expect(screen.getByTestId("comment-body").textContent).toContain(
      "@Maya Chen",
    );
    const reactions = screen.getAllByTestId("comment-reaction");
    expect(reactions).toHaveLength(3);
    expect(reactions[0].textContent).toBe("👍1");
    // Mentioned in the thread: may resolve; not the author: no edit / delete.
    expect(screen.getByTestId("comment-resolve")).toBeTruthy();
    expect(screen.queryByTestId("comment-edit-open")).toBeNull();
    expect(screen.queryByTestId("comment-delete")).toBeNull();
    fireEvent.click(reactions[1]);
    expect(patch).toHaveBeenCalledWith({
      commentId: "c1",
      patch: { react: "✅" },
    });
    fireEvent.click(screen.getByTestId("comment-resolve"));
    expect(patch).toHaveBeenCalledWith({
      commentId: "c1",
      patch: { resolved: true },
    });
  });

  it("hides Resolve when the server says no, and folds resolved threads", () => {
    data = base({
      threads: [
        {
          root: comment({
            resolved: true,
            resolved_by: { id: "u-asha", name: "Asha Rao" },
          }),
          replies: [],
          can_resolve: false,
          mention_ids: [],
        },
      ],
    });
    renderIt();
    expect(screen.getByTestId("comment-thread-resolved").textContent).toContain(
      "Resolved by Asha Rao",
    );
    expect(screen.getByTestId("comment-thread-resolved").textContent).toContain(
      "Resolved by Asha Rao · Asha Rao's comment on expected_reply: “@Maya Chen check the tool-call eval”",
    );
    expect(screen.queryByTestId("comment-reopen")).toBeNull();
  });

  it("lets the author edit and delete, and keeps a deleted root with replies", () => {
    data = base({
      threads: [
        {
          root: comment({ author: { id: "u-me", name: "Maya Chen" } }),
          replies: [],
          can_resolve: true,
          mention_ids: [],
        },
        {
          root: comment({ id: "c2", deleted: true, body: "" }),
          replies: [comment({ id: "c3", parent_id: "c2", body: "still here" })],
          can_resolve: false,
          mention_ids: [],
        },
      ],
    });
    renderIt();
    expect(screen.getByText("Comment deleted")).toBeTruthy();
    expect(screen.getByText("still here")).toBeTruthy();
    fireEvent.click(screen.getAllByTestId("comment-delete")[0]);
    expect(del).toHaveBeenCalledWith("c1");
  });

  it("posts from the composer with the anchor; ⌘↵ posts", async () => {
    renderIt();
    const input = screen.getByTestId("comment-composer-input");
    fireEvent.change(input, {
      target: { value: "Looks right", selectionStart: 11 },
    });
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith({
        body: "Looks right",
        anchor: { kind: "row" },
      }),
    );
  });

  it("mentions through the picker and warns about people without access", async () => {
    renderIt();
    const input = screen.getByTestId("comment-composer-input");
    fireEvent.change(input, { target: { value: "@Ali", selectionStart: 4 } });
    const options = await screen.findAllByTestId("mention-option");
    expect(options.map((o) => o.textContent)).toEqual([
      expect.stringContaining("Alice Park · No access"),
    ]);
    fireEvent.mouseDown(options[0]);
    // The box shows the name; the server gets the token.
    await waitFor(() => expect(input.value).toBe("@Alice Park "));
    expect(screen.getByTestId("mention-no-access").textContent).toBe(
      "Alice Park can't view this dataset. An admin can give access in Dataset settings › Access.",
    );
    fireEvent.change(input, {
      target: { value: "@Alice Park please look", selectionStart: 23 },
    });
    fireEvent.click(screen.getByTestId("comment-composer-submit"));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0].body).toBe(
      "@[Alice Park](user:u-alice) please look",
    );
  });

  it("puts the caret in the composer when asked (⌘K Comment on this row)", async () => {
    const focused = vi.fn();
    renderIt({ focusComposer: "t-1", onComposerFocused: focused });
    const input = screen.getByTestId("comment-composer-input");
    await waitFor(() => expect(document.activeElement).toBe(input));
    expect(focused).toHaveBeenCalledTimes(1);
  });

  it("shows the counter near the server's limit and blocks over it", () => {
    renderIt();
    const input = screen.getByTestId("comment-composer-input");
    fireEvent.change(input, { target: { value: "x".repeat(1880) } });
    expect(screen.getByTestId("comment-composer-counter").textContent).toBe(
      "120 characters left",
    );
    fireEvent.change(input, { target: { value: "x".repeat(2001) } });
    expect(screen.getByTestId("comment-composer-submit").disabled).toBe(true);
  });
});

describe("CommentThreads with the row's result-row threads", () => {
  it("names each thread's run in its anchor chip", () => {
    data = base({
      threads: [
        {
          root: comment({
            id: "c9",
            run_id: "run-4a",
            anchor: {
              kind: "step",
              span_id: "s3",
              step_label: "3 lookup_order",
            },
          }),
          replies: [],
          can_resolve: true,
          mention_ids: [],
          run_label: "#4A",
        },
      ],
      run_id: "all",
      run_label: null,
    });
    renderIt({ includeRuns: true });
    expect(screen.getByTestId("comment-anchor").textContent).toBe(
      "on #4A · step 3 lookup_order",
    );
  });

  it("says the rest is under All when only a deleted root with replies is left", () => {
    data = base({
      threads: [
        {
          root: comment({ id: "c2", deleted: true, body: "" }),
          replies: [comment({ id: "c3", parent_id: "c2", body: "still here" })],
          can_resolve: false,
          mention_ids: [],
        },
      ],
      open: 0,
    });
    renderIt({ status: "open", includeRuns: true });
    expect(
      screen.getByText("No open threads. All shows the rest."),
    ).toBeTruthy();
    expect(screen.queryByText("Every thread here is resolved.")).toBeNull();
  });

  it("keeps where a deleted root was, and its thread still takes replies", async () => {
    data = base({
      threads: [
        {
          root: comment({ id: "c2", deleted: true, body: "" }),
          replies: [comment({ id: "c3", parent_id: "c2", body: "still here" })],
          can_resolve: false,
          mention_ids: [],
        },
      ],
      open: 0,
    });
    renderIt({ includeRuns: true });
    expect(screen.getByTestId("comment-deleted").textContent).toContain(
      "on expected_reply",
    );
    fireEvent.click(screen.getByTestId("comment-reply"));
    const input = screen.getByTestId("comment-reply-composer-input");
    fireEvent.change(input, { target: { value: "Picking this up." } });
    fireEvent.click(screen.getByTestId("comment-reply-composer-submit"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith({
        body: "Picking this up.",
        parent_id: "c2",
      }),
    );
  });
});
