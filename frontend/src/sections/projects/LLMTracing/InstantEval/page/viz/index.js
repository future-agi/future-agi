// The Ask page's charts. Each takes
//   {summary, records?, questions, focus, onFocus, onSelect, onOpen,
//    running, window, unit}
// where `summary` is the run's counts (GET /tracer/instant-eval/summary/),
// `records` the rows when there are few enough to draw one square each
// (1,500 or fewer), focus = {configId, answer | null}, onSelect(filter, label)
// narrows the rows to a filter the rows endpoint understands, and onOpen(id)
// opens one row. Without a summary, the charts build one from `records`.
export { default as AskTimeline } from "./AskTimeline";
export { default as AskPatterns } from "./AskPatterns";
export { default as AskHeatmap } from "./AskHeatmap";
export { default as AskCrossTab } from "./AskCrossTab";
export { findPatterns, fisherExact, benjaminiHochberg } from "./askStats";
export { buildTimeline, chooseBinMs } from "./timelineBins";
export {
  buildMatrix,
  buildModelDayGrid,
  buildTimeGrid,
  PIVOTS,
} from "./askPivot";
export { answerState, defaultAnswer, resolveFocus } from "./askAnswers";
export {
  SQUARES_MAX,
  matchesFilter,
  summarizeRecords,
  groupKeysOf,
} from "./askSummary";
