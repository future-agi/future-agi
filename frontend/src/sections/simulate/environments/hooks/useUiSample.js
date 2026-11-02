import { useSearchParams } from "react-router-dom";

// `?uiSample=gaps,versions` (or `all`) swaps the "soon" placeholders on a real
// environment for the prototype's sample-filled design, so it can be reviewed
// and compared with Vel's prototype. Every view it fills carries UiOnlyFlag.
export default function useUiSample(feature) {
  const [params] = useSearchParams();
  const raw = params.get("uiSample");
  if (!raw) return false;
  const list = raw.split(",").map((s) => s.trim());
  return list.includes("all") || list.includes(feature);
}
