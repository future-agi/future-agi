const activeStepIndex = (steps, stage) => {
  const index = steps.findIndex((step) => step.stage === stage);
  return index >= 0 ? index : 0;
};

const isInternalHref = (href) =>
  typeof href === "string" && href.startsWith("/") && !href.startsWith("//");

export const hrefWithJourneyGuide = (href, step, { replay = false } = {}) => {
  if (!isInternalHref(href) || !step?.tourAnchor) {
    return href;
  }

  const [withoutHash, hash] = href.split("#");
  const [pathname, query = ""] = withoutHash.split("?");
  const params = new URLSearchParams(query);
  params.set("tour_anchor", step.tourAnchor);
  params.set("journey_step", step.id || step.stage);
  if (replay) {
    params.set("tour_replay", "1");
  }

  return `${pathname}?${params.toString()}${hash ? `#${hash}` : ""}`;
};

export const journeyCurrentStep = (journeyPlan, stage) => {
  const steps = journeyPlan?.steps || [];
  if (!steps.length) return null;

  const derivedCurrentIndex =
    typeof journeyPlan.currentStepIndex === "number"
      ? journeyPlan.currentStepIndex
      : activeStepIndex(steps, stage);
  const currentIndex = Math.min(
    Math.max(derivedCurrentIndex, 0),
    steps.length - 1,
  );
  return steps[currentIndex];
};
