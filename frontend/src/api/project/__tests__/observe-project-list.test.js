import { beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import {
  fetchAllObserveProjects,
  OBSERVE_PROJECT_PAGE_SIZE,
  OBSERVE_PROJECT_REQUEST_TIMEOUT_MS,
  readObserveProjectPage,
} from "../observe-project-list";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    project: { projectObserveList: "/tracer/project/list_projects/" },
  },
}));

const project = (id) => ({ id: `project-${id}`, name: `Project ${id}` });

const pageResponse = (pageNumber, totalPages, totalRows, rows) => ({
  data: {
    status: true,
    result: {
      metadata: {
        total_rows: totalRows,
        page_number: pageNumber,
        page_size: OBSERVE_PROJECT_PAGE_SIZE,
        total_pages: totalPages,
      },
      table: rows,
    },
  },
});

describe("fetchAllObserveProjects", () => {
  beforeEach(() => vi.clearAllMocks());

  it("follows every bounded server page and returns the complete project set", async () => {
    axios.get
      .mockResolvedValueOnce(
        pageResponse(
          0,
          3,
          205,
          Array.from({ length: 100 }, (_, index) => project(index)),
        ),
      )
      .mockResolvedValueOnce(
        pageResponse(
          1,
          3,
          205,
          Array.from({ length: 100 }, (_, index) => project(index + 100)),
        ),
      )
      .mockResolvedValueOnce(
        pageResponse(
          2,
          3,
          205,
          Array.from({ length: 5 }, (_, index) => project(index + 200)),
        ),
      );

    const signal = new AbortController().signal;
    const projects = await fetchAllObserveProjects({
      signal,
      params: { sort_by: "name", page_size: 500, project_type: "experiment" },
    });

    expect(projects).toHaveLength(205);
    expect(projects.at(-1)).toEqual(project(204));
    expect(axios.get).toHaveBeenCalledTimes(3);
    for (let pageNumber = 0; pageNumber < 3; pageNumber += 1) {
      expect(axios.get).toHaveBeenNthCalledWith(
        pageNumber + 1,
        "/tracer/project/list_projects/",
        {
          signal: expect.any(AbortSignal),
          timeout: OBSERVE_PROJECT_REQUEST_TIMEOUT_MS,
          params: {
            sort_by: "name",
            project_type: "observe",
            page_number: pageNumber,
            page_size: 100,
          },
        },
      );
    }
  });

  it("deduplicates a row repeated across numbered pages", async () => {
    axios.get
      .mockResolvedValueOnce(pageResponse(0, 2, 101, [project(1), project(2)]))
      .mockResolvedValueOnce(pageResponse(1, 2, 101, [project(2), project(3)]));

    await expect(fetchAllObserveProjects()).resolves.toEqual([
      project(1),
      project(2),
      project(3),
    ]);
  });

  it("fails closed on a malformed page instead of publishing a partial picker", async () => {
    axios.get.mockResolvedValueOnce({
      data: {
        status: true,
        result: {
          metadata: { page_number: 0, page_size: 100 },
          table: [project(1)],
        },
      },
    });

    await expect(fetchAllObserveProjects()).rejects.toThrow(
      "Observe project list returned an invalid page contract",
    );
  });

  it("bounds the entire multi-page picker action, not every page independently", async () => {
    vi.useFakeTimers();
    let secondSignal;
    axios.get
      .mockResolvedValueOnce(pageResponse(0, 2, 101, [project(1)]))
      .mockImplementationOnce(
        (_url, { signal }) =>
          new Promise(() => {
            secondSignal = signal;
          }),
      );

    const pending = fetchAllObserveProjects();
    const rejection = expect(pending).rejects.toMatchObject({
      code: "aggregation_request_timeout",
    });
    await vi.advanceTimersByTimeAsync(OBSERVE_PROJECT_REQUEST_TIMEOUT_MS);

    await rejection;
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(secondSignal.aborted).toBe(true);
    vi.useRealTimers();
  });

  it("reads one requested page through the same validated transport wall", async () => {
    axios.get.mockResolvedValueOnce(pageResponse(2, 3, 205, [project(201)]));

    await expect(
      readObserveProjectPage({
        params: { page_number: 2, page_size: OBSERVE_PROJECT_PAGE_SIZE },
      }),
    ).resolves.toMatchObject({
      rows: [project(201)],
      totalRows: 205,
      totalPages: 3,
    });
  });
});
