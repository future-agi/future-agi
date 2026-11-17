// W3-7: every create door's last line, one wording.
import { fmtCount } from "src/sections/common/format/fmt";

/** `Creates "Refund review" at v1 · 1 row · 2 columns · note: Started blank · 2 columns`. */
export const v1LineText = (v1) =>
  v1
    ? `Creates "${v1.name}" at v1 · ${fmtCount(v1.rows, "row")} · ${fmtCount(v1.columns, "column")} · note: ${v1.note}`
    : "";
