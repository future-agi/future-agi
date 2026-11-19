import { useState } from "react";
import PropTypes from "prop-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SkillDocumentEditor from "../../editor/SkillDocumentEditor";
import useDraftSession from "../../editor/useDraftSession";

const api = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock("src/api/skills", () => ({
  useSaveSkillDraft: () => ({ mutateAsync: api.save }),
  getSkillErrorCode: (error) => error.code,
}));
vi.mock("../../editor/SkillFileEditor", () => {
  function Source({ file, onChange, readOnly }) {
    return (
      <textarea
        aria-label="Full source"
        value={file.content}
        readOnly={readOnly}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  Source.propTypes = {
    file: PropTypes.shape({ content: PropTypes.string }).isRequired,
    onChange: PropTypes.func.isRequired,
    readOnly: PropTypes.bool,
  };
  return { default: Source };
});

const root =
  "---\nname: policy\ndescription: Original routing\n---\nOriginal clause";
const initial = {
  baseVersion: 4,
  revision: 7,
  note: "Keep note",
  files: [
    { path: "SKILL.md", content: root },
    { path: "references/policy.md", content: "Keep reference bytes\r\n" },
  ],
};
function Session({ id = "synthetic", draft = initial }) {
  // The existing session remains the only mutable owner and the only saver.
  const session = useDraftSession(id, draft, true);
  const [acknowledged, setAcknowledged] = useState("not-run");
  return (
    <>
      <SkillDocumentEditor
        file={session.files[0]}
        onChange={(content) =>
          session.edit(
            session.files.map((file) =>
              file.path === "SKILL.md" ? { ...file, content } : file,
            ),
          )
        }
      />
      <button
        type="button"
        onClick={async () => {
          const saved = await session.flush();
          setAcknowledged(saved ? String(saved.revision) : "blocked");
        }}
      >
        Flush current draft
      </button>
      <output data-testid="acknowledged">{acknowledged}</output>
      <output data-testid="state">
        {JSON.stringify({
          files: session.files,
          dirty: session.dirty,
          revision: session.revision,
          status: session.status,
        })}
      </output>
    </>
  );
}
Session.propTypes = { id: PropTypes.string, draft: PropTypes.object };
const state = () => JSON.parse(screen.getByTestId("state").textContent);
const body = () => screen.getByRole("textbox", { name: "Instructions" });
const description = () =>
  screen.getByRole("textbox", {
    name: "When should the agent use this skill?",
  });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("document projections use the existing save/flush sequence", () => {
  it("flushes description and body edits queued after an in-flight save with one revision owner", async () => {
    const first = deferred();
    api.save
      .mockImplementationOnce(() => first.promise)
      .mockImplementation(async (request) => ({
        ...request,
        revision: request.revision + 1,
      }));
    render(<Session />);
    fireEvent.change(body(), { target: { value: "First clause" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Flush current draft" }),
    );
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1));
    fireEvent.change(description(), { target: { value: "New routing" } });
    fireEvent.change(body(), { target: { value: "Later clause" } });
    expect(state().dirty).toBe(true);
    expect(state().revision).toBe(7);
    await act(async () =>
      first.resolve({ ...api.save.mock.calls[0][0], revision: 8 }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("acknowledged")).toHaveTextContent("9"),
    );
    expect(api.save).toHaveBeenCalledTimes(2);
    expect(api.save.mock.calls[0][0].files[0].content).toBe(
      root.replace("Original clause", "First clause"),
    );
    expect(api.save.mock.calls[1][0]).toEqual({
      ...initial,
      revision: 8,
      files: [
        {
          path: "SKILL.md",
          content: root
            .replace("Original routing", '"New routing"')
            .replace("Original clause", "Later clause"),
        },
        initial.files[1],
      ],
    });
    expect(state().dirty).toBe(false);
  });

  it("keeps failed-save content and returns no runnable snapshot on conflict", async () => {
    api.save.mockRejectedValue({ code: "draft_conflict" });
    render(<Session />);
    fireEvent.change(description(), {
      target: { value: "Unsent private change" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Flush current draft" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("acknowledged")).toHaveTextContent("blocked"),
    );
    expect(state().status).toBe("conflict");
    expect(state().dirty).toBe(true);
    expect(description()).toHaveValue("Unsent private change");
    fireEvent.click(
      screen.getByRole("button", { name: "Flush current draft" }),
    );
    expect(api.save).toHaveBeenCalledTimes(1);
  });

  it("does not save on view switches and marks changes dirty immediately, before any request", () => {
    render(<Session />);
    fireEvent.click(screen.getByRole("tab", { name: "Preview" }));
    fireEvent.click(screen.getByRole("tab", { name: "Source" }));
    fireEvent.click(screen.getByRole("tab", { name: "Document" }));
    expect(state().dirty).toBe(false);
    fireEvent.change(description(), { target: { value: "Changed routing" } });
    expect(state().dirty).toBe(true);
    expect(state().revision).toBe(7);
    expect(api.save).not.toHaveBeenCalled();
  });

  it("does not apply a late old-scope save to a newly mounted session", async () => {
    const pending = deferred();
    api.save.mockImplementation(() => pending.promise);
    const { rerender } = render(<Session key="scope-one" />);
    fireEvent.change(body(), { target: { value: "Old scope edit" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Flush current draft" }),
    );
    const next = {
      ...initial,
      revision: 20,
      files: [
        {
          path: "SKILL.md",
          content: root.replace("Original clause", "Other scope content"),
        },
      ],
    };
    rerender(<Session key="scope-two" id="other-scope" draft={next} />);
    await act(async () => pending.resolve({ ...initial, revision: 8 }));
    expect(body()).toHaveValue("Other scope content");
    expect(state().revision).toBe(20);
    expect(state().dirty).toBe(false);
    expect(screen.getByTestId("acknowledged")).toHaveTextContent("not-run");
  });
});
