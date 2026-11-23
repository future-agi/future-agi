// A copied step link names the API's step key here; the drawers open on Participants for it.
export const STEP_PARAM = "participantsStep";

export const linkedStepKey = () =>
  new URLSearchParams(window.location.search).get(STEP_PARAM);

export const openingTab = () => (linkedStepKey() ? "workers" : "transcript");
