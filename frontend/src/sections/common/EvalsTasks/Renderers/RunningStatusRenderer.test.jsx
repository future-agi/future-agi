/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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

const RESUME_ICON = "material-symbols-light:resume-outline-rounded";

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
  return { api, data };
};

const resumeButton = () =>
  screen.queryByText(RESUME_ICON)?.closest("button") ?? null;

describe("RunningStatusRenderer resume action", () => {
  beforeEach(() => {
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { status: true } });
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

  it.each(["completed", "running", "pending"])(
    "offers no Resume on a %s task",
    (status) => {
      renderStatus(status);

      expect(resumeButton()).toBeNull();
    },
  );
});
