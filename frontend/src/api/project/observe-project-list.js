import axios, { endpoints } from "src/utils/axios";

// list_projects deliberately caps a single page at 100 rows. Consumers that
// need a complete picker/catalog must follow its numbered pagination instead
// of asking the server for an oversized page (which is rejected with HTTP 400).
export const OBSERVE_PROJECT_PAGE_SIZE = 100;

const parseProjectPage = (response, requestedPage) => {
  const body = response?.data;
  const result = body?.result;
  const metadata = result?.metadata;

  if (
    body?.status !== true ||
    !Array.isArray(result?.table) ||
    !metadata ||
    !Number.isInteger(metadata.total_pages) ||
    metadata.total_pages < 0 ||
    metadata.page_number !== requestedPage ||
    metadata.page_size !== OBSERVE_PROJECT_PAGE_SIZE
  ) {
    throw new Error("Observe project list returned an invalid page contract");
  }

  return { rows: result.table, totalPages: metadata.total_pages };
};

/**
 * Fetch every accessible Observe project using the endpoint's bounded pages.
 *
 * The backend owns `project_type`, `page_number`, and `page_size`; callers may
 * supply other filters/sorts without weakening the 100-row request contract.
 */
export async function fetchAllObserveProjects({ signal, params = {} } = {}) {
  const projects = [];
  const seenProjectIds = new Set();
  let pageNumber = 0;
  let totalPages = 1;

  while (pageNumber < totalPages) {
    const response = await axios.get(endpoints.project.projectObserveList, {
      signal,
      params: {
        ...params,
        project_type: "observe",
        page_number: pageNumber,
        page_size: OBSERVE_PROJECT_PAGE_SIZE,
      },
    });
    const page = parseProjectPage(response, pageNumber);
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
}
