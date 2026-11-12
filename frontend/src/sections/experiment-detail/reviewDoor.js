// L7c g6 (grid:review-rows, HIDE): the results and Summary review doors
// offer no `Current filter` row source. StartReviewDialog (g8) counted a
// page filter against the rubric run only (`Current filter (0)` under
// `Fails Groundedness · #2A` on #2B) and started no queue; until it reads
// the filter the way the grid does, these doors pass no filters, so the
// dialog offers All rows / Selected only (each counted and started right).
export const NO_REVIEW_FILTER = Object.freeze([]);
