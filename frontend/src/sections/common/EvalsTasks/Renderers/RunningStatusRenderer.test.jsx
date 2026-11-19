/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "src/components/snackbar";
import RunningStatusRenderer from "./RunningStatusRenderer";

const axiosPostMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    default: { post: axiosPostMock },
    endpoints: {
      project: {
        pauseEvalTask: (taskId) => `/tasks/${taskId}/pause`,
        // The real mapping, so the click below is checked against the URL the
        // backend serves rather than a stand-in.
        resumeEvalTask: actual.endpoints.project.resumeEvalTask,
      },
    },
  };
});

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

const RESUME_ICON = "solar:play-linear";

const renderStatus = (status) => {
  const api = {
    applyServerSideTransaction: vi.fn(),
    refreshServerSide: vi.fn(),
  };
  const data = { id: `task-${status}`, status };
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RunningStatusRenderer value={status} data={data} api={api} />
    </QueryClientProvider>,
  );
  return { api, data, queryClient };
};

const resumeButton = () =>
  screen.queryByText(RESUME_ICON)?.closest("button") ?? null;

describe("RunningStatusRenderer resume action", () => {
  beforeEach(() => {
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { status: true } });
    enqueueSnackbar.mockReset();
  });

  it.each(["failed", "paused"])(
    "offers Resume on a %s task and resumes it by id",
    async (status) => {
      const { api, data } = renderStatus(status);

      expect(resumeButton()).not.toBeNull();
      fireEvent.click(resumeButton());

      await waitFor(() =>
        expect(axiosPostMock).toHaveBeenCalledWith(
          `/tracer/eval-task/unpause_eval_task/?eval_task_id=${data.id}`,
          {},
        ),
      );
      await waitFor(() =>
        expect(api.applyServerSideTransaction).toHaveBeenCalled(),
      );
    },
  );

  it("discards cached Logs when resuming from the grid", async () => {
    const { data, queryClient } = renderStatus("failed");
    const logs = ["eval-task-logs", data.id];
    queryClient.setQueryData(logs, {
      status: "failed",
      failure_reason: "Selection failed",
    });
    fireEvent.click(resumeButton());
    await waitFor(() => expect(queryClient.getQueryData(logs)).toBeUndefined());
  });

  it("shows the server's reason when Resume is refused", async () => {
    const refusal =
      "The run couldn't start after an automatic retry. Your task is saved.";
    axiosPostMock.mockRejectedValueOnce({
      response: { status: 400, data: { status: false, message: refusal } },
    });
    renderStatus("failed");
    fireEvent.click(resumeButton());
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(refusal, {
        variant: "error",
      }),
    );
  });

  it("keeps the generic text when a refusal carries no message", async () => {
    axiosPostMock.mockRejectedValueOnce(new Error("network"));
    renderStatus("failed");
    fireEvent.click(resumeButton());
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Failed to resume task. It may have already finished.",
        { variant: "error" },
      ),
    );
  });

  it.each(["completed", "running", "pending"])(
    "offers no Resume on a %s task",
    (status) => {
      renderStatus(status);

      expect(resumeButton()).toBeNull();
    },
  );
});
