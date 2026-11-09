// L3 (the capability contract): a verbatim copy of dev's experiment Data page and the
// dataset Experiments list, so non-prototype builds show the results page that
// already works on the real backend. The DSX tree under
// sections/experiment-detail stays the prototype's.
//
// PORT_SOURCE is the origin/dev commit L0 merged into dataset integration
// (423dc5d5e6, second parent). Later dev fixes to
// frontend/src/sections/experiment-detail/{ExperimentWrapperView, ExperimentRow,
// ExperimentBar, ExperimentContextProvider, experiment-context,
// DevelopExperimentSelect}, ExperimentBarRightSection/*, ExperimentData/* and
// sections/develop-detail/ExperimentTab/{ExperimentTab,ExperimentTabHeader}
// are not picked up automatically: re-sync from a newer dev commit and update
// this sha (post-launch item: fold the copy back once the DSX results land).
// Data-field and SDK empty-state updates synchronized through this integration
// revision; launch gating and the single create entry point remain local.
export const PORT_SOURCE = "ff345800f3dfb0b8e6602b69e1e916f0c02c367a";
