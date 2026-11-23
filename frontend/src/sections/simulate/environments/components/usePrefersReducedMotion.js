import { useMediaQuery } from "@mui/material";

// MUI's Collapse, Fade and Drawer don't turn themselves off for reduced
// motion: pass `timeout={reduce ? 0 : ...}` from this.
export default function usePrefersReducedMotion() {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}
