/* eslint-disable react/prop-types */
// W1 walk fix (row 11, finding 5): the re-score confirm never traps the
// user. While the count loads, after it fails, and when the server answers
// 200 with no plan (today's rerun-cells answer, "Cell rerun has started."),
// Cancel and Escape close it. A failed count says so in plain words and
// still lets the user go ahead; a Re-score on vN stays refused but closes.
// Two quick presses of Re-score send one request.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
import { useRescore } from "../useRescore";

const state = vi.hoisted(() => ({ post: vi.fn(), notify: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: state.post },
}));
vi.mock("src/sections/common/ux/toast", async (original) => ({
  ...(await original()),
  notify: state.notify,
}));

// What rerun-cells answers today, dry_run or not.
const STARTED = {
  data: {
    status: true,
    result: { message: "Cell rerun has started.", workflow_id: "wf-1" },
  },
};
const runs = [{ run_id: "r1", label: "#2A", experiment_id: "x1" }];
const ask = (extra = {}) => ({
  runs,
  evalIdsOf: () => ["u1"],
  evalNames: ["Tone"],
  ...extra,
});
const TITLE = "Re-score #2A with Tone?";
const STILL = "You can still press Re-score to go ahead, or Cancel.";

function Harness({ request, onResult }) {
  const rescore = useRescore({ datasetId: "d1" });
  return (
    <>
      <button
        type="button"
        onClick={() => rescore.request(request).then(onResult)}
      >
        Open re-score
      </button>
      {rescore.dialog}
    </>
  );
}

const clients = [];
function open(request) {
  const onResult = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <ThemeProvider
      theme={createTheme({
        palette: palette("light"),
        customShadows: { drawer: "none" },
      })}
    >
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Harness request={request} onResult={onResult} />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open re-score" }));
  return onResult;
}

afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  state.post.mockReset();
  state.notify.mockReset();
});

describe("W1: the re-score confirm never traps the user", () => {
  it("closes on Escape while the count is still loading", async () => {
    state.post.mockReturnValue(new Promise(() => {}));
    const onResult = open(ask());
    const dialog = await screen.findByRole("dialog", { name: TITLE });
    expect(
      within(dialog).getByText("Counting judge calls…"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeEnabled();
    // Confirm waits for the count; only Cancel and Escape act now.
    expect(
      within(dialog).getByRole("button", { name: "Re-score" }),
    ).toBeDisabled();

    fireEvent.keyDown(dialog, { key: "Escape" });

    await waitFor(() =>
      expect(onResult).toHaveBeenCalledWith({ cancelled: true }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: TITLE }),
      ).not.toBeInTheDocument(),
    );
    // Only the count itself was asked for; no re-score was sent.
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][1]).toMatchObject({ dry_run: true });
  });

  it("says in plain words when the count fails, and both buttons work", async () => {
    state.post
      .mockRejectedValueOnce(new Error(""))
      .mockResolvedValueOnce(STARTED);
    const onResult = open(ask());
    const dialog = await screen.findByRole("dialog", { name: TITLE });
    expect(await within(dialog).findByText(STILL)).toBeInTheDocument();
    expect(
      within(dialog).getByText("Could not read what this costs"),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText("Counting judge calls…"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeEnabled();
    const go = within(dialog).getByRole("button", { name: "Re-score" });
    expect(go).toBeEnabled();

    fireEvent.click(go);

    await waitFor(() => expect(onResult).toHaveBeenCalled());
    expect(onResult.mock.calls[0][0]?.cancelled).toBeUndefined();
    expect(state.post).toHaveBeenCalledTimes(2);
    const [url, body] = state.post.mock.calls[1];
    expect(url).toBe(endpoints.develop.experiment.reRunExperimentCell("x1"));
    expect(body.dry_run).toBeUndefined();
    expect(body).toMatchObject({
      run_ids: ["r1"],
      user_eval_metric_ids: ["u1"],
    });
    // The server only started it: the toast says so, not a garbled result.
    expect(state.notify).toHaveBeenCalledWith(
      "Tone started",
      expect.objectContaining({ variant: "success" }),
    );
  });

  it("reads a 200 with no plan as a count it could not read, never an endless load", async () => {
    state.post.mockResolvedValueOnce(STARTED);
    open(ask());
    const dialog = await screen.findByRole("dialog", { name: TITLE });
    expect(
      await within(dialog).findByText("Could not read what this costs"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(STILL)).toBeInTheDocument();
    expect(
      within(dialog).queryByText("Counting judge calls…"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeEnabled();
    expect(
      within(dialog).getByRole("button", { name: "Re-score" }),
    ).toBeEnabled();
  });

  it("keeps a Re-score on v7 refused when its plan cannot be read, and Cancel still closes it", async () => {
    state.post.mockRejectedValueOnce(new Error(""));
    const onResult = open(ask({ datasetVersion: 7 }));
    const dialog = await screen.findByRole("dialog", { name: TITLE });
    expect(
      await within(dialog).findByText("Could not read what this costs"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Re-score" }),
    ).toBeDisabled();
    expect(within(dialog).queryByText(STILL)).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(onResult).toHaveBeenCalledWith({ cancelled: true }),
    );
    expect(state.post).toHaveBeenCalledTimes(1);
  });

  it("sends one re-score for two quick presses of Re-score", async () => {
    state.post
      .mockResolvedValueOnce({
        data: {
          result: {
            plan: {
              runs: [{ run_id: "r1", next_label: "#2A·2" }],
              by_model: [{ model: "gpt-4o", calls: 3 }],
              judge_calls: 3,
              cells: 3,
            },
          },
        },
      })
      .mockResolvedValue({
        data: {
          result: {
            unchanged: false,
            eval_names: ["Tone"],
            labels: ["#2A·2"],
            runs: [
              {
                run_id: "n1",
                from_run_id: "r1",
                label: "#2A·2",
                from_label: "#2A",
              },
            ],
          },
        },
      });
    const onResult = open(ask());
    const dialog = await screen.findByRole("dialog", { name: TITLE });
    const go = within(dialog).getByRole("button", { name: "Re-score" });
    await waitFor(() => expect(go).toBeEnabled());

    // Both presses land before React re-renders the button as busy.
    act(() => {
      go.click();
      go.click();
    });

    await waitFor(() => expect(onResult).toHaveBeenCalled());
    // One count (dry_run) and one re-score.
    expect(state.post).toHaveBeenCalledTimes(2);
    expect(state.post.mock.calls[1][1].dry_run).toBeUndefined();
    expect(state.notify).toHaveBeenCalledTimes(1);
    expect(state.notify.mock.calls[0][0]).toMatch(/^Re-scored #2A with Tone/);
  });
});
