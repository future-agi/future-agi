// The shared theme paints a focused, raised field label with the paper colour
// and 5 px of side padding (theme/overrides/components/select.js): a mask for
// a label that sits in the field's outline. Gateway key fields put the label
// above the field (theme/overrides/components/textfield.js), so the mask only
// shows: a dark band in dark mode and a 5 px shift. An inline style outranks
// that rule for these labels only; the theme is unchanged.
export const LABEL_ABOVE_STYLE = { backgroundColor: "transparent", padding: 0 };
