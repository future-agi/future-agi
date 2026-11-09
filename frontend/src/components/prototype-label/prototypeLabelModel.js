// the capability contract: every page of a prototype build carries one label. This module
// holds its copy and the one space token every reserving style reads; the
// component (PrototypeLabel.jsx) is loaded only by prototype builds.

/** The label's visible text, exactly. */
export const PROTOTYPE_LABEL_TEXT = "Prototype: mock data, resets per browser";

/** The accessible name of the label's landmark. */
export const PROTOTYPE_LABEL_NAME = "Prototype notice";

/** The CSS variable the label's height lives in (0px when it is absent). */
export const PROTOTYPE_LABEL_VAR = "--fa-proto-label-h";

/** The label's height: one 12 px line with its padding. */
export const PROTOTYPE_LABEL_HEIGHT = "24px";

const H = `var(${PROTOTYPE_LABEL_VAR}, 0px)`;

/**
 * The global styles that reserve the label's strip so it never covers content
 * or controls: the token on :root, the document pushed below the strip, and
 * the viewport-fixed layers (side drawers, full-screen dialogs, the progress
 * bar) started below it. `body` raises specificity above the components' own
 * single-class rules, which are injected later.
 */
export function prototypeLabelGlobalStyles() {
  return {
    ":root": { [PROTOTYPE_LABEL_VAR]: PROTOTYPE_LABEL_HEIGHT },
    body: { paddingTop: H },
    // Columns of the dashboard row that the shell does not size itself
    // (the Falcon sidebar).
    "body [data-fa-shell='row'] > *": {
      maxHeight: `calc(100vh - ${H})`,
    },
    "body .MuiDrawer-modal > .MuiDrawer-paperAnchorLeft, body .MuiDrawer-modal > .MuiDrawer-paperAnchorRight":
      {
        top: H,
        height: `calc(100% - ${H})`,
      },
    "body .MuiDialog-paperFullScreen": {
      marginTop: H,
      height: `calc(100% - ${H})`,
      maxHeight: `calc(100% - ${H})`,
    },
    "body #nprogress .bar": { top: H },
  };
}
