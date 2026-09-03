import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";
import {
  AUTOMATION_RULE_LIST_PAGE_SIZE as CONFIGURED_AUTOMATION_RULE_LIST_PAGE_SIZE,
  INTERACTIVE_REQUEST_TIMEOUT_MS,
} from "src/config/runtime_limits";

export const AUTOMATION_RULE_LIST_PAGE_SIZE =
  CONFIGURED_AUTOMATION_RULE_LIST_PAGE_SIZE;
export const AUTOMATION_RULE_LIST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

const invalidAutomationRulePage = () => {
  const error = new Error("Automation rules returned an invalid page");
  error.code = "automation_rule_list_invalid_page";
  return error;
};

/**
 * Read and validate one automation-rule page before it can enter the infinite
 * query cache. A failed next page leaves every already-published page intact.
 */
export async function readAutomationRulePage(requestPage, upstreamSignal) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestPage({
        signal,
        timeout: AUTOMATION_RULE_LIST_TIMEOUT_MS,
      }),
    {
      timeoutMs: AUTOMATION_RULE_LIST_TIMEOUT_MS,
      signal: upstreamSignal,
    },
  );
  const payload = response?.data?.result ?? response?.data;
  const results = payload?.results;
  const count = payload?.count;
  const currentPage = payload?.current_page;
  const totalPages = payload?.total_pages;
  const expectedTotalPages = Number.isSafeInteger(count)
    ? Math.max(1, Math.ceil(count / AUTOMATION_RULE_LIST_PAGE_SIZE))
    : null;

  if (
    !Array.isArray(results) ||
    results.some(
      (rule) =>
        !rule ||
        typeof rule !== "object" ||
        rule.id === null ||
        rule.id === undefined,
    ) ||
    !Number.isSafeInteger(count) ||
    count < 0 ||
    results.length > AUTOMATION_RULE_LIST_PAGE_SIZE ||
    results.length > count ||
    !Number.isSafeInteger(currentPage) ||
    currentPage < 1 ||
    !Number.isSafeInteger(totalPages) ||
    totalPages !== expectedTotalPages ||
    currentPage > totalPages
  ) {
    throw invalidAutomationRulePage();
  }

  return { results, count, currentPage, totalPages };
}
