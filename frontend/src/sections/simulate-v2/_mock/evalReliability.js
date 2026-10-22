/*
  How far an evaluation can be trusted as an improvement's goal: how often
  it agrees with people who labelled the same conversations (Cohen's κ,
  which discounts agreement by chance), and how many conversations they
  marked broken it still passed. A search scored on an evaluation that
  agrees at chance can "improve" by fooling it — so the goal menu says so
  before anything runs. Read from the workspace's annotations; drawn by hash
  here, so it holds still for an evaluation.
*/
import { hashSeed } from "./runStream";

export const KAPPA_RELIABLE = 0.6;
export const KAPPA_WEAK = 0.4;

const KAPPAS = [0.08, 0.12, 0.27, 0.46, 0.63, 0.78, 0.84];

export const evalReliabilityOf = (evalId) => {
  if (!evalId) return null;
  const h = hashSeed(`eval-reliability:${evalId}`);
  /* Some evaluations have no labelled conversations to check against. */
  if (h % 7 === 0) return { measured: false, tier: "unmeasured", label: "Not checked" };
  const kappa = KAPPAS[h % KAPPAS.length];
  const labelled = 60 + (h % 9) * 20;
  const broken = 10 + (h % 3) * 5;
  /* An evaluation near chance passes most of what people marked broken. */
  const passedBroken = Math.round(broken * Math.max(0.05, 0.75 - kappa * 0.85));
  const tier = kappa >= KAPPA_RELIABLE ? "reliable" : kappa >= KAPPA_WEAK ? "moderate" : "unreliable";
  return {
    measured: true,
    kappa,
    labelled,
    broken,
    passedBroken,
    tier,
    label: { reliable: "Reliable", moderate: "Moderate", unreliable: "Near chance" }[tier],
  };
};

/** One line on what choosing it as the goal means. */
export const evalReliabilityLine = (r) => {
  if (!r) return null;
  if (!r.measured) return "Not checked against your annotations yet — a search can only be as right as the evaluation it's scored on. Label a few conversations in Annotations to check it.";
  const stat = `κ ${r.kappa.toFixed(2)} on ${r.labelled} labelled conversations; passed ${r.passedBroken} of ${r.broken} that annotators marked broken`;
  if (r.tier === "unreliable") return `Agrees with your annotators about as often as chance (${stat}). A search scored on it can improve by fooling it — pick the pass rate or an outcome, or calibrate this evaluation first.`;
  if (r.tier === "moderate") return `Agrees with your annotators only moderately (${stat}). Check the winner's conversations by hand before shipping.`;
  return `Agrees with your annotators (${stat}).`;
};
