// MUI elevation scale, rebuilt on the Future AGI Design System: in-page
// surfaces are flat, low elevations get a hairline lift, floating layers use the
// overlay shadow and the top of the scale the modal shadow (ds-tokens.js).
import { dsCustomShadows } from "./ds-tokens";

export function shadows(mode) {
  const s = dsCustomShadows(mode);
  const scale = ["none"];
  for (let i = 1; i <= 24; i += 1) {
    if (i <= 2) scale.push(s.z1);
    else if (i <= 12) scale.push(s.dropdown);
    else scale.push(s.dialog);
  }
  return scale;
}
