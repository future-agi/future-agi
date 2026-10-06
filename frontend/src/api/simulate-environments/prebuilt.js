import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { SIMULATE_ENVIRONMENTS_KEY } from "./environments";

// The shared template library: the same system templates for every user. Reading or
// opening one creates nothing; the first edit or run in its workspace makes a copy.
const templatesPath = () =>
  apiPath("/simulate/api/harness-environment-templates/");
const templatePath = (slug) =>
  apiPath("/simulate/api/harness-environment-templates/{slug}/", { slug });

export const prebuiltEnvironmentsQueryKey = () => [
  ...SIMULATE_ENVIRONMENTS_KEY,
  "prebuilt",
];
export const prebuiltEnvironmentKey = (slug) => [
  ...prebuiltEnvironmentsQueryKey(),
  slug,
];

// The one place a server template becomes the card the library renders.
export const templateToCard = (template) => ({
  id: template.slug,
  // Where the template opens, read-only, as a workspace; the first edit or run copies it.
  environmentId: template.environment_id,
  name: template.name,
  surface: template.surface,
  agentType: template.surface === "voice" ? "voice_platform" : "chat_webhook",
  domain: template.domain,
  tagline: template.description,
  description: template.description,
  scenarioCount: template.scenario_count,
  tools: (template.tools ?? []).map((tool) => ({
    name: tool.name,
    desc: tool.description,
  })),
  rules: template.rules ?? [],
  evalPreset: template.evaluations ?? [],
  scenarios: template.scenarios,
});

export function usePrebuiltEnvironments() {
  return useQuery({
    queryKey: prebuiltEnvironmentsQueryKey(),
    queryFn: async () => (await axios.get(templatesPath())).data,
    select: (data) => (data?.results ?? []).map(templateToCard),
  });
}

// One template with its generated scenarios, for the detail pane.
export function usePrebuiltEnvironment(slug) {
  return useQuery({
    queryKey: prebuiltEnvironmentKey(slug),
    queryFn: async () => (await axios.get(templatePath(slug))).data,
    select: templateToCard,
    enabled: Boolean(slug),
  });
}
