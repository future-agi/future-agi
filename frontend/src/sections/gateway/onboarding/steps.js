import { paths } from "src/routes/paths";

// Why "Try a model" is disabled for a role below Owner/Admin: only they can
// run a test request through the gateway (ModelTryIt). The step still shows
// it, so every role sees the same steps.
export const TRY_MODEL_ADMIN_ONLY =
  "Only an organization Owner or Admin can run models here.";

// Why "Try a model" is disabled where the gateway does not serve Try it, when
// the caller gives no reason of its own. The model's Try it tab says the same.
export const TRY_IT_UNAVAILABLE = "Try it is not available in this release.";

function countStatus(count) {
  if (!Number.isSafeInteger(count) || count < 0) return "unknown";
  return count > 0 ? "ready" : "not-ready";
}

function representedStatus(value) {
  return ["ready", "not-ready"].includes(value) ? value : "unknown";
}

// No step is always complete. Legacy counts remain accepted, but callers with
// full replies pass readiness so saved/unpushed sources and inactive keys do
// not count. Managed enablement alone never proves a usable credit balance.
export function onboardingSteps({
  providerCount = 0,
  keyCount = 0,
  requestCount,
  managedOn = false,
  managedOffered = false,
  readiness,
  requestQuery,
  canRunConsole = false,
  tryModelHref = null,
  // Whether the gateway serves Try it (delegated_playground_available true).
  // Try a model opens the model's Try it tab, so it is a link only then.
  tryItAvailable = false,
  tryItUnavailableReason = TRY_IT_UNAVAILABLE,
} = {}) {
  const gw = paths.dashboard.gateway;
  const source = readiness
    ? representedStatus(readiness.source)
    : providerCount > 0
      ? "ready"
      : managedOn && managedOffered ? "unknown" : countStatus(providerCount);
  const key = readiness ? representedStatus(readiness.key) : countStatus(keyCount);
  const request = requestQuery
    ? requestQuery.isError || requestQuery.error || requestQuery.isPending || requestQuery.isLoading
      ? "unknown"
      : countStatus(requestQuery.data)
    : countStatus(requestCount);
  const creditsReady = readiness?.managed === "ready";
  const consoleTry = canRunConsole && tryModelHref && tryItAvailable;
  const requestGuide = consoleTry
    ? "Use the Endpoint card's snippet with your API key, or try a model in the console (Owner or Admin only). Then check again; failed requests do not complete setup."
    : "Copy the Endpoint card's snippet and run it with your personal API key. Then check again; failed requests do not complete setup.";

  return [
    {
      id: "models",
      title: creditsReady ? "Add a provider or use Future AGI credits" : "Add a provider",
      description: source === "unknown"
        ? "Could not confirm a usable model source. Check again or review Providers."
        : creditsReady
          ? "Use Future AGI credits for credit-eligible catalog models, or add your own provider keys under Providers."
          : canRunConsole
            ? "Add your own provider key and apply it to the gateway. Saved providers must be pushed before you can use them."
            : "Ask an Owner or Admin to add and apply a provider key for your organization.",
      status: source,
      done: source === "ready",
      cta: creditsReady ? "Open the catalog" : canRunConsole ? "Add a provider" : "View providers",
      href: creditsReady ? gw.models : gw.providers,
    },
    {
      id: "key",
      title: "Create an API key",
      description: key === "unknown"
        ? "Could not confirm a usable API key. Check again or review your keys."
        : "Create a personal API key for your application. Revoked, expired or pending keys are not ready to use.",
      status: key,
      done: key === "ready",
      cta: "Create a key",
      href: `${gw.keys}?create=1`,
    },
    {
      id: "request",
      title: "First successful request",
      description: request === "unknown"
        ? `${requestQuery?.isFetching ? "Checking for a successful request…" : "Could not check successful requests. Check again to retry."} ${requestGuide}`
        : requestGuide,
      status: request,
      done: request === "ready",
      cta: "Open Logs",
      href: gw.logs,
      // Where Try it does not run, that is the reason for every role.
      tryModel: !tryModelHref
        ? null
        : consoleTry
          ? { cta: "Try a model", href: tryModelHref }
          : {
              cta: "Try a model",
              href: tryModelHref,
              disabled: true,
              reason: tryItAvailable
                ? TRY_MODEL_ADMIN_ONLY
                : tryItUnavailableReason || TRY_IT_UNAVAILABLE,
            },
    },
  ];
}
