import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import rules from "../__fixtures__/rules.json";
import review from "../__fixtures__/review-submission.json";
import ChangeNotesEditor from "../components/ChangeNotesEditor";
import DecisionForm from "../components/DecisionForm";
import { approveBlockers } from "../lib/decision";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const RULES = "/omega-hub/v1/rules";
const DECISION = `/omega-hub/v1/review/submissions/${review.id}/decision`;

beforeEach(() => respond([["get", RULES, rules]]));

const approveButton = () => screen.getByTestId("hub-decision-submit");

describe("Decision form (SPEC §5.3, §5.4)", () => {
  it("takes its checklist from GET /rules", async () => {
    renderAt(<DecisionForm submission={review} />);
    const items = await screen.findAllByTestId("hub-checklist-item");
    expect(items.map((el) => el.getAttribute("data-key"))).toEqual(
      rules.checklist.map((c) => c.key),
    );
    for (const item of rules.checklist)
      expect(screen.getByText(item.text)).toBeInTheDocument();
  });

  it("keeps Approve disabled until every flag, every checklist item and every observed part is done", async () => {
    renderAt(<DecisionForm submission={review} />);
    await screen.findAllByTestId("hub-checklist-item");
    expect(approveButton()).toBeDisabled();

    for (const box of screen.getAllByTestId("hub-flag-ack"))
      fireEvent.click(box);
    expect(approveButton()).toBeDisabled();
    for (const box of screen.getAllByTestId("hub-checklist-item"))
      fireEvent.click(box);
    // The draft says "None." under Shell while the checks observed a shell call.
    expect(screen.getByTestId("hub-approve-blockers")).toHaveTextContent(
      "shell",
    );
    expect(approveButton()).toBeDisabled();

    fireEvent.change(screen.getByTestId("hub-disclosure-shell-input"), {
      target: { value: "Runs git --version only with --git." },
    });
    await waitFor(() => expect(approveButton()).toBeEnabled());

    // Answering a flag group No takes approval away again.
    fireEvent.click(screen.getAllByTestId("hub-flag-no")[0]);
    expect(approveButton()).toHaveTextContent("Request changes");
    fireEvent.click(screen.getByTestId("hub-decision-approve-tab"));
    expect(approveButton()).toBeDisabled();
    expect(screen.getByTestId("hub-approve-blockers")).toHaveTextContent(
      "change 1 No answer to Yes",
    );
  });

  it("sends the current rules version, every flag and the confirmed disclosure", async () => {
    let sent;
    respond([
      ["get", RULES, rules],
      [
        "post",
        DECISION,
        ({ data }) => {
          sent = data;
          return { status: "approved" };
        },
      ],
    ]);
    const onDecided = vi.fn();
    renderAt(<DecisionForm submission={review} onDecided={onDecided} />);
    await screen.findAllByTestId("hub-checklist-item");
    for (const box of screen.getAllByTestId("hub-flag-ack"))
      fireEvent.click(box);
    for (const box of screen.getAllByTestId("hub-checklist-item"))
      fireEvent.click(box);
    fireEvent.change(screen.getByTestId("hub-disclosure-shell-input"), {
      target: { value: "Runs git --version only with --git." },
    });
    fireEvent.click(approveButton());
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(sent.decision).toBe("approve");
    expect(sent.rulesVersion).toBe(rules.version);
    expect(Object.keys(sent.checklist).sort()).toEqual(
      rules.checklist.map((c) => c.key).sort(),
    );
    expect(sent.acknowledgedFlags).toEqual([...review.flags].sort());
    expect(sent.disclosure.shell).toBe("Runs git --version only with --git.");
    expect(sent.notes).toBeUndefined();
  });

  it('shows "Rules changed, reload" when the server answers 409', async () => {
    respond([
      ["get", RULES, rules],
      [
        "post",
        DECISION,
        {
          error: {
            code: "OMEGA_HUB_RULES_CHANGED",
            message: "The rules changed",
            fix: "",
          },
        },
        409,
      ],
    ]);
    renderAt(<DecisionForm submission={review} />);
    await screen.findAllByTestId("hub-checklist-item");
    fireEvent.click(screen.getByTestId("hub-decision-reject-tab"));
    fireEvent.change(screen.getByTestId("hub-decision-reason-input"), {
      target: { value: "This is not materially different from its origin." },
    });
    fireEvent.click(approveButton());
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Reject",
        exact: true,
      }),
    );
    expect(await screen.findByTestId("hub-rules-changed")).toHaveTextContent(
      "Rules changed, reload",
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await screen.findByTestId("hub-decision-submit");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("needs a 20-character reason to reject or request changes", async () => {
    renderAt(<DecisionForm submission={review} />);
    await screen.findAllByTestId("hub-checklist-item");
    fireEvent.click(screen.getByTestId("hub-decision-reject-tab"));
    const reason = screen.getByTestId("hub-decision-reason-input");
    fireEvent.change(reason, { target: { value: "Too short." } });
    expect(approveButton()).toBeDisabled();
    fireEvent.change(reason, {
      target: { value: "Twenty characters at least." },
    });
    expect(approveButton()).toBeEnabled();
    fireEvent.click(screen.getByTestId("hub-decision-changes-tab"));
    expect(approveButton()).toBeEnabled();
  });

  it("agrees with the server's gap rule for blank and None. parts", () => {
    const base = {
      flags: [],
      acknowledged: [],
      checklistItems: [{ key: "a" }],
      checklist: { a: true },
      observed: { shell: ["main.mjs:3"] },
    };
    const disclosure = {
      summary: "A summary that is long enough.",
      network: "None.",
      files: "None.",
      shell: "none",
      background: "None.",
      credentials: "None.",
    };
    expect(approveBlockers({ ...base, disclosure }).join(" ")).toContain(
      "shell",
    );
    expect(
      approveBlockers({
        ...base,
        disclosure: { ...disclosure, shell: "Runs git." },
      }),
    ).toEqual([]);
  });
});

describe("Change notes editor (SPEC §5.4)", () => {
  const files = [{ path: "main.mjs" }, { path: "README.md" }];

  it("offers only files from the submission's tree", () => {
    renderAt(
      <ChangeNotesEditor notes={[]} onChange={() => {}} files={files} />,
    );
    fireEvent.mouseDown(
      within(screen.getByTestId("hub-change-notes-editor")).getAllByRole(
        "combobox",
      )[0],
    );
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["README.md", "main.mjs"]);
  });

  it("caps the notes at 30", () => {
    const thirty = Array.from({ length: 30 }, (_, i) => ({
      file: "main.mjs",
      line: i + 1,
      note: `note ${i}`,
    }));
    renderAt(
      <ChangeNotesEditor notes={thirty} onChange={() => {}} files={files} />,
    );
    expect(screen.getByTestId("hub-change-notes-full")).toBeInTheDocument();
    expect(screen.queryByTestId("hub-change-note-add")).toBeNull();
  });

  it("adds a file-and-line note", () => {
    const onChange = vi.fn();
    renderAt(
      <ChangeNotesEditor notes={[]} onChange={onChange} files={files} />,
    );
    fireEvent.change(screen.getByTestId("hub-change-note-line"), {
      target: { value: "12" },
    });
    fireEvent.change(screen.getByTestId("hub-change-note-text"), {
      target: { value: "Say so in disclosure.shell." },
    });
    fireEvent.click(screen.getByTestId("hub-change-note-add"));
    expect(onChange).toHaveBeenCalledWith([
      { file: "README.md", line: 12, note: "Say so in disclosure.shell." },
    ]);
  });
});
