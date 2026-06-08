import { readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import {
  DESTINATION_TOUR_ANCHORS,
  DESTINATION_TOUR_STEP_COPY,
  destinationTourProgressForStep,
} from "./destinationTourAnchorConfig";

const activationFlowPath = path.resolve(
  process.cwd(),
  "../futureagi/accounts/services/onboarding/activation_flow.yml",
);
const activationFlow = parse(readFileSync(activationFlowPath, "utf8"));

const journeySteps = Object.values(activationFlow.journeys).flatMap(
  (journey) => journey.steps,
);

describe("destinationTourAnchorConfig", () => {
  it("has focused copy for every configured journey step", () => {
    const configuredStepIds = journeySteps.map((step) => step.id);
    const missingCopy = configuredStepIds.filter(
      (stepId) => !DESTINATION_TOUR_STEP_COPY[stepId],
    );

    expect(missingCopy).toEqual([]);
  });

  it("tracks the configured journey anchor contract", () => {
    const configuredAnchors = activationFlow.tour_anchors;
    const supportedAnchors = new Set(DESTINATION_TOUR_ANCHORS);
    const configuredAnchorSet = new Set(configuredAnchors);

    const missingAnchors = configuredAnchors.filter(
      (anchor) => !supportedAnchors.has(anchor),
    );
    const staleAnchors = DESTINATION_TOUR_ANCHORS.filter(
      (anchor) => !configuredAnchorSet.has(anchor),
    );

    expect(missingAnchors).toEqual([]);
    expect(staleAnchors).toEqual([]);
  });

  it("derives loop progress from journey step or destination anchor", () => {
    expect(
      destinationTourProgressForStep({
        journeyStep: "run_gateway_request",
        tourAnchor: "gateway_request_button",
      }),
    ).toEqual({
      currentLabel: "See cost + latency per call",
      nextLabel: "Trace where time and spend went",
      planTitle: "Route one request safely",
      stepCount: 6,
      stepNumber: 3,
    });

    expect(
      destinationTourProgressForStep({
        tourAnchor: "sample_trace_link",
      }),
    ).toMatchObject({
      currentLabel: "Review issue",
      nextLabel: "Connect real data",
      planTitle: "Sample loop",
      stepCount: 3,
      stepNumber: 2,
    });
  });

  it("uses user-facing quality-check copy for eval and trace steps", () => {
    expect(DESTINATION_TOUR_STEP_COPY.create_eval_dataset).toMatchObject({
      label: "Choose source",
    });
    expect(DESTINATION_TOUR_STEP_COPY.create_trace_evaluator).toMatchObject({
      label: "Create quality check",
    });
    expect(DESTINATION_TOUR_STEP_COPY.eval_next_loop).toMatchObject({
      label: "Fix or finish",
    });
    expect(
      destinationTourProgressForStep({
        journeyStep: "create_trace_evaluator",
        tourAnchor: "observe_evaluator_button",
      }),
    ).toMatchObject({
      currentLabel: "Create quality check",
      planTitle: "Observe loop",
    });
  });
});
