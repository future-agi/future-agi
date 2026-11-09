// One-view primitives (ONE-VIEW-SPEC §3), built once by lane G. Area lanes
// import from here (or the files) and never re-implement a primitive.
// Documented in sections/common/PRIMITIVES.md › "One view — G".

// §3.1 persistence
export {
  default as useLayoutPrefs,
  layoutKey,
  readLayout,
  writeLayout,
  mergeLayout,
  normalizeLayout,
  emptyLayout,
  isEmptyLayout,
  getLayout,
  setLayout,
  useLayoutStore,
  useLayoutKey,
  useLayoutUserId,
  removeLegacyModeKeys,
  LAYOUT_VERSION,
} from "./layoutPrefs";

// §3.2 column shaping
export * from "./columnLayout";
export { default as useColumnLayout } from "./useColumnLayout";
export { default as ColumnsMenu, ColumnsPanel } from "./ColumnsMenu";
export { openColumnsMenu, hasColumnsHost } from "./columnsMenuHost";
export {
  default as HeaderMenu,
  useHeaderMenu,
  HeaderMenuButton,
  HeaderMenuCell,
  isMenuKey,
  menuTargetOf,
} from "./HeaderMenu";
export { headerMenuItems, toAgMenuItems } from "./headerMenuItems";
export {
  default as useAgColumnShaping,
  isUserColumnEvent,
} from "./useAgColumnShaping";

// §3.3 sections
export {
  default as Section,
  useSections,
  useSectionRegistry,
  sectionDefaultOpen,
} from "./Section";

// §3.4 outline
export {
  default as useOutline,
  setOutline,
  outlineIsOpen,
  OUTLINE_MAX,
} from "./useOutline";
export { default as OutlineToggle } from "./OutlineToggle";
export { default as ExpandAllButton } from "./ExpandAllButton";

// §3.5 depth in place
export { default as InlineDetail } from "./InlineDetail";
export { default as HoverCard } from "./HoverCard";

// §3.6 action bar
export { default as ActionBar, actionsFor } from "./ActionBar";
export { fitActions, primaryCount, ACTION_GAP, MORE_WIDTH } from "./fitActions";

// §3.7 view menu
export { default as ViewMenu } from "./ViewMenu";

// §3.8 context menu
export { default as ContextMenu, useContextMenu } from "./ContextMenu";
export * from "./contextItems";
export { ItemsMenu, MenuRow, arrangeMenuItems, capsOf } from "./MenuItems";

// §3.9 hints
export {
  default as Hint,
  HintRecall,
  useHintStore,
  showTipsOnThisPage,
  visibleHintId,
  hintsKey,
  readDismissed,
  writeDismissed,
} from "./Hint";

// §3.10 viewport defaults + hover selection
export {
  default as useViewportDensity,
  viewportDensityOf,
  SHORT_SCREEN,
  TALL_SCREEN,
} from "./useViewportDensity";
export {
  default as useHoverSelection,
  hoverSelectClass,
  HOVER_SELECT_CLASS,
  HAS_SELECTION_CLASS,
} from "./useHoverSelection";

// Shell
export {
  default as LegacyParamScrub,
  scrubLegacyParams,
  LEGACY_PARAMS,
} from "./LegacyParamScrub";
export { default as OneViewStyles } from "./OneViewStyles";
