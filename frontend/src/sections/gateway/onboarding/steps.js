import { paths } from "src/routes/paths";

// The three things a new organization does before the gateway is useful.
// Every step can be undone, so none is "always done": a "Gateway connected"
// step would be true for every org whose gateway row exists, and is not here.
//
// Managed credit offers require both deployment availability and organization
// enablement. Otherwise the first step is adding an own-key provider.
//
// `tryModelHref` is the Try it tab of the first model one of the org's
// providers serves (null when none does): Try it sends a request from the
// console, with no API key, so the first request step offers it.
export function onboardingSteps({
  providerCount = 0,
  managedOn = false,
  managedOffered = false,
  keyCount = 0,
  requestCount = 0,
  tryModelHref = null,
} = {}) {
  const gw = paths.dashboard.gateway;
  const models =
    managedOffered && managedOn
      ? {
          id: "models",
          title: "Add a provider or use Future AGI credits",
          description:
            "Use Future AGI credits for credit-eligible catalog models, or add your own provider keys under Providers.",
          done: providerCount > 0 || managedOn,
          cta: "Open the catalog",
          href: gw.models,
        }
      : {
          id: "models",
          title: "Add a provider",
          description:
            "Add your own provider key; the gateway calls that provider's models with it.",
          done: providerCount > 0,
          cta: "Add a provider",
          href: gw.providers,
        };
  return [
    models,
    {
      id: "key",
      title: "Create an API key",
      description: "Your applications send it with every request.",
      done: keyCount > 0,
      cta: "Create a key",
      href: `${gw.keys}?create=1`,
    },
    {
      id: "request",
      title: "Send your first request",
      description: tryModelHref
        ? "Use the Endpoint card's snippet, or try a model here in the console; the request shows up in Logs within seconds."
        : "Use the Endpoint card's snippet; the request shows up in Logs within seconds.",
      done: requestCount > 0,
      cta: "Open Logs",
      href: gw.logs,
      tryModel: tryModelHref
        ? { cta: "Try a model", href: tryModelHref }
        : null,
    },
  ];
}
