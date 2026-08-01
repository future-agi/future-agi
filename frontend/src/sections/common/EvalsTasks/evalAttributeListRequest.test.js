import { afterEach, describe, expect, it, vi } from "vitest";

import axios, { endpoints } from "src/utils/axios";
import {
  fetchEvalAttributeList,
  getEvalAttributeListQueryKey,
} from "./evalAttributeListRequest";

const PROJECT_ID = "1372e742-a10b-4d98-9ca4-31ef4d67115f";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("eval task attribute-list requests", () => {
  it("uses only the supported project scope when creating a task", async () => {
    const get = vi.spyOn(axios, "get").mockResolvedValue({ data: {} });

    await fetchEvalAttributeList(PROJECT_ID, "traces");

    expect(get).toHaveBeenCalledWith(endpoints.project.getEvalAttributeList(), {
      params: {
        filters: JSON.stringify({ project_id: PROJECT_ID }),
        row_type: "traces",
      },
    });
  });

  it("uses only the supported project scope when editing a task", async () => {
    const get = vi.spyOn(axios, "get").mockResolvedValue({ data: {} });

    await fetchEvalAttributeList(PROJECT_ID, "sessions");

    expect(get).toHaveBeenCalledWith(endpoints.project.getEvalAttributeList(), {
      params: {
        filters: JSON.stringify({ project_id: PROJECT_ID }),
        row_type: "sessions",
      },
    });
  });

  it("keys discovery by project and row type, not mutable task filters", () => {
    expect(getEvalAttributeListQueryKey(PROJECT_ID, "spans")).toEqual([
      "eval-attributes",
      PROJECT_ID,
      "spans",
    ]);
  });
});
