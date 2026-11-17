/* eslint-disable react/prop-types */
// The re-score confirm on the new experiment page: when the server answers
// with a sentence (Update Evaluation on an experiment that calls an HTTP
// endpoint saves the change and scores nothing), the toast is that
// sentence, never "<evaluation> started". An answer that only starts the
// work still says it started.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
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

const EDIT_URL = "/model-hub/develops/d1/edit_and_run_user_eval/u1/";
const HTTP_SAVED =
  "Saved. This experiment calls an HTTP endpoint, so a row is scored with this change only when it is sent again. Retry failed rows from the Data tab or a failed cell; to score every row, create a new experiment. The scores shown are from before your change.";
const TITLE = "Update quality and re-score?";
const CONFIRM = "Update and re-score";

const ask = () => ({
  posts: [{ url: EDIT_URL, body: { run: true, experiment_id: "x1" } }],
  evalNames: ["quality"],
  experimentId: "x1",
  estimate: false,
  title: () => TITLE,
  confirmLabel: () => CONFIRM,
});

function Harness({ onResult }) {
  const rescore = useRescore({ datasetId: "d1" });
  return (
    <>
      <button
        type="button"
        onClick={() => rescore.request(ask()).then(onResult)}
      >
        Open re-score
      </button>
      {rescore.dialog}
    </>
  );
}

const clients = [];
function open() {
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
          <Harness onResult={onResult} />
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

describe("the re-score confirm shows the server's own sentence", () => {
  it("shows an HTTP experiment's saved-not-scored sentence, not '<evaluation> started'", async () => {
    state.post.mockResolvedValue({
      data: { status: true, result: HTTP_SAVED },
    });
    const onResult = open();
    const dialog = await screen.findByRole("dialog", { name: TITLE });

    fireEvent.click(within(dialog).getByRole("button", { name: CONFIRM }));

    await waitFor(() => expect(onResult).toHaveBeenCalled());
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][0]).toBe(EDIT_URL);
    expect(state.notify).toHaveBeenCalledTimes(1);
    expect(state.notify).toHaveBeenCalledWith(
      HTTP_SAVED,
      expect.objectContaining({ variant: "info" }),
    );
    expect(state.notify).not.toHaveBeenCalledWith(
      "quality started",
      expect.anything(),
    );
  });

  it("still says it started when the server only starts the work", async () => {
    state.post.mockResolvedValue({
      data: {
        status: true,
        result: { message: "Eval rerun has started.", workflow_id: "wf-1" },
      },
    });
    const onResult = open();
    const dialog = await screen.findByRole("dialog", { name: TITLE });

    fireEvent.click(within(dialog).getByRole("button", { name: CONFIRM }));

    await waitFor(() => expect(onResult).toHaveBeenCalled());
    expect(state.notify).toHaveBeenCalledWith(
      "quality started",
      expect.objectContaining({ variant: "success" }),
    );
  });
});
