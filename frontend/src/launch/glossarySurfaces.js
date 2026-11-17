// The surfaces each glossary term explains. Once the glossary itself shows
// (palette.glossary), a launch build lists a term in the `?` sheet and the
// command palette only while one of its surfaces shows (launchSwitch.js
// glossaryShownIn), so the glossary never names a page the build hides. A
// term with no entry never shows; a feature that ships adds its terms here.
// Pure data: no imports, so tests read it as is.

const MATRIX = "exp.results.matrix";
const SUMMARY = "exp.results.summary";
const PATHS = "exp.results.paths";
const TRACES = "exp.results.traces";
const ITERATE = "exp.results.iterateDock";
const COMPARE = "exp.compare";
const LEADERBOARDS = "exp.leaderboards";
const WIZARD = "exp.new";
const RUN_SETTINGS = "exp.new.runSettings";
const SOURCES = "exp.new.runningSources";
const CONNECT = "runners.connect";
const SPLITS = "dataset.splits";
const FROM_PRODUCTION = "dataset.fromProduction";
const AGREEMENT = "evals.agreement";
const DAG = "evals.dag";
const REVIEWS = "dataset.collab.reviews";
// Task suites and stand-in buttons exist only in prototype builds.
const PROTOTYPE = "shell.protoOnly";
// The dataset grid and evals of every build.
const SHOWN = "shell.real";

const on = (...ids) => Object.freeze(ids);

/** @type {Readonly<Record<string, readonly string[]>>} */
export const GLOSSARY_TERM_SURFACES = Object.freeze({
  successRate: on(MATRIX, SUMMARY, LEADERBOARDS),
  passAt1: on(MATRIX, SUMMARY, ITERATE),
  passHatK: on(MATRIX, SUMMARY, ITERATE),
  reliable: on(MATRIX, SUMMARY),
  flaky: on(MATRIX, COMPARE),
  trial: on(MATRIX, COMPARE, ITERATE),
  run: on(MATRIX, SUMMARY),
  experiment: on(MATRIX, SUMMARY),
  baseline: on(MATRIX, SUMMARY, COMPARE),
  delta: on(MATRIX, SUMMARY, COMPARE),
  ci95: on(SUMMARY, LEADERBOARDS),
  withinNoise: on(SUMMARY, LEADERBOARDS),
  regressed: on(SUMMARY, COMPARE),
  improved: on(MATRIX, SUMMARY),
  // Its copy points at agreement with reviewers.
  judge: on(AGREEMENT),
  check: on(MATRIX, COMPARE, DAG),
  costPerTask: on(MATRIX, SUMMARY, LEADERBOARDS),
  totalCost: on(MATRIX, SUMMARY, LEADERBOARDS),
  timePerTask: on(MATRIX, SUMMARY, LEADERBOARDS),
  firstWrongStep: on(COMPARE),
  firstDifference: on(COMPARE),
  milestone: on(COMPARE),
  differ: on(COMPARE, PATHS),
  carried: on(MATRIX, ITERATE),
  confidence: on(SUMMARY, LEADERBOARDS),
  preview: on(PROTOTYPE),
  mcp: on(SOURCES, CONNECT),
  executionMode: on(SOURCES),
  traceIds: on(TRACES),
  captureMode: on(TRACES),
  graphQuality: on(PATHS, TRACES),
  declaredParam: on(SOURCES),
  sideEffects: on(SOURCES, CONNECT),
  pathShare: on(PATHS),
  graphDiff: on(PATHS),
  latencyP90: on(MATRIX, SUMMARY),
  ttfw: on(MATRIX, SUMMARY),
  bargeIn: on(MATRIX, SUMMARY),
  wer: on(MATRIX, SUMMARY),
  talkRatio: on(MATRIX, SUMMARY),
  deadAir: on(MATRIX, SUMMARY),
  textOnlyDepth: on(TRACES, SUMMARY),
  fullVoiceDepth: on(TRACES, SUMMARY),
  otelPassthrough: on(CONNECT),
  worse: on(MATRIX, SUMMARY, COMPARE),
  anyEvalWorse: on(MATRIX, SUMMARY),
  aligned: on(COMPARE),
  onlyFails: on(MATRIX, COMPARE),
  runBar: on(MATRIX),
  whyColumn: on(MATRIX),
  // Results views do not enable review: both terms follow its own capability.
  pairwise: on(REVIEWS),
  blind: on(REVIEWS),
  costPerSolved: on(MATRIX),
  bestValue: on(MATRIX),
  bothRan: on(LEADERBOARDS, COMPARE),
  rowChecks: on(PROTOTYPE),
  carriedFrom: on(MATRIX, ITERATE),
  liveMock: on(MATRIX, TRACES),
  lanes: on(COMPARE),
  mapKey: on(COMPARE),
  keptTalking: on(MATRIX, SUMMARY),
  pairDiffers: on(MATRIX),
  level: on(SUMMARY, COMPARE),
  tooCloseToCall: on(SUMMARY, COMPARE),
  isolate: on(ITERATE),
  paths: on(PATHS),
  matrix: on(MATRIX),
  tries: on(MATRIX, ITERATE),
  newest: on(LEADERBOARDS),
  previousVersion: on(LEADERBOARDS, COMPARE),
  noRunReliable: on(MATRIX, SUMMARY),
  p50: on(MATRIX, SUMMARY),
  p90: on(MATRIX, SUMMARY),
  endStateChecks: on(PROTOTYPE),
  dryRun: on(RUN_SETTINGS),
  startState: on(PROTOTYPE),
  asTasks: on(PROTOTYPE),
  asConversations: on(FROM_PRODUCTION),
  splits: on(SPLITS),
  // The experiment wizard's Expected answers column.
  expectedAnswers: on(WIZARD),
  wrapLines: on(SHOWN),
  matchedByMeaning: on(SHOWN),
});
