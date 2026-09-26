import axios, { endpoints } from "src/utils/axios";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";
import {
  INTERACTIVE_REQUEST_TIMEOUT_MS,
  OBSERVE_PROJECT_PAGE_SIZE as CONFIGURED_OBSERVE_PROJECT_PAGE_SIZE,
} from "src/config/runtime_limits";

// list_projects deliberately caps a single page at 100 rows. Consumers that
// need a complete picker/catalog must follow its numbered pagination instead
// of asking the server for an oversized page (which is rejected with HTTP 400).
export const OBSERVE_PROJECT_PAGE_SIZE = CONFIGURED_OBSERVE_PROJECT_PAGE_SIZE;
export const OBSERVE_PROJECT_REQUEST_TIMEOUT_MS =
  INTERACTIVE_REQUEST_TIMEOUT_MS;

const projectPageError = () => {
  const error = new Error(
    "Observe project list returned an invalid page contract",
  );
  error.code = "observe_project_list_invalid_page";
  return error;
};

const parseProjectPage = (response, requestedPage, requestedPageSize) => {
  const body = response?.data;
  const result = body?.result;
  const metadata = result?.metadata;
  const expectedTotalPages = Number.isSafeInteger(metadata?.total_rows)
    ? Math.ceil(metadata.total_rows / requestedPageSize)
    : null;

  if (
    body?.status !== true ||
    !Array.isArray(result?.table) ||
    !metadata ||
    !Number.isSafeInteger(metadata.total_rows) ||
    metadata.total_rows < 0 ||
    !Number.isSafeInteger(metadata.total_pages) ||
    metadata.total_pages < 0 ||
    metadata.page_number !== requestedPage ||
    metadata.page_size !== requestedPageSize ||
    metadata.total_pages !== expectedTotalPages ||
    result.table.length > requestedPageSize ||
    result.table.length > metadata.total_rows
  ) {
    throw projectPageError();
  }

  return {
    rows: result.table,
    totalRows: metadata.total_rows,
    totalPages: metadata.total_pages,
    metadata,
    response,
  };
};

/** Read one numbered project page under the visible-action wall. */
export async function readObserveProjectPage({
  signal,
  params = {},
  timeoutMs = OBSERVE_PROJECT_REQUEST_TIMEOUT_MS,
} = {}) {
  const requestedPage = params.page_number ?? 0;
  const requestedPageSize = params.page_size ?? OBSERVE_PROJECT_PAGE_SIZE;
  const response = await awaitAggregationRequestWithDeadline(
    (requestSignal) =>
      axios.get(endpoints.project.projectObserveList, {
        signal: requestSignal,
        timeout: timeoutMs,
        params: {
          ...params,
          project_type: "observe",
          page_number: requestedPage,
          page_size: requestedPageSize,
        },
      }),
    { timeoutMs, signal },
  );
  return parseProjectPage(response, requestedPage, requestedPageSize);
}

/**
 * Fetch every accessible Observe project using the endpoint's bounded pages.
 *
 * The backend owns `project_type`, `page_number`, and `page_size`; callers may
 * supply other filters/sorts without weakening the 100-row request contract.
 */
export async function fetchAllObserveProjects({ signal, params = {} } = {}) {
  return awaitAggregationRequestWithDeadline(
    async (actionSignal) => {
      const projects = [];
      const seenProjectIds = new Set();
      let pageNumber = 0;
      let totalPages = 1;

      while (pageNumber < totalPages) {
        const page = await readObserveProjectPage({
          signal: actionSignal,
          timeoutMs: OBSERVE_PROJECT_REQUEST_TIMEOUT_MS,
          params: {
            ...params,
            page_number: pageNumber,
            page_size: OBSERVE_PROJECT_PAGE_SIZE,
          },
        });
        totalPages = page.totalPages;

        page.rows.forEach((project) => {
          const projectId = project?.id == null ? null : String(project.id);
          if (projectId === null || seenProjectIds.has(projectId)) return;
          seenProjectIds.add(projectId);
          projects.push(project);
        });

        pageNumber += 1;
      }

      return projects;
    },
    { timeoutMs: OBSERVE_PROJECT_REQUEST_TIMEOUT_MS, signal },
  );
}
