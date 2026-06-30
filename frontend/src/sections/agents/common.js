import { pinCodeOptions } from "src/components/agent-definitions/helper";

export const getAgentFormValues = (agentDetails) => {
  const snapshot = agentDetails?.configuration_snapshot || {};

  const rawContactNumber = snapshot?.contact_number || "";
  let matchedPin = "";
  let localNumber = rawContactNumber;

  if (rawContactNumber.startsWith("+")) {
    const sortedCodes = [...(pinCodeOptions ?? [])].sort(
      (a, b) => b.value.length - a.value.length,
    );

    for (const option of sortedCodes) {
      if (rawContactNumber.startsWith(`+${option.value}`)) {
        matchedPin = option.value;
        localNumber = rawContactNumber.replace(`+${option.value}`, "");
        break;
      }
    }
  }

  return {
    // Basic Information
    agentName: snapshot.agent_name || "",
    agentType: snapshot.agent_type || "",
    languages: snapshot.languages ?? ["en"],

    // Configuration
    provider: snapshot.provider?.toLowerCase() || "",
    assistantId: snapshot.assistant_id || "",
    // apiEndpoint: snapshot.api_endpoint || "",
    authenticationMethod: snapshot.authentication_method || "",
    apiKey: agentDetails?.api_key ?? "",
    observabilityEnabled: snapshot.observability_enabled ?? false,
    token: snapshot?.token || "",
    username: snapshot?.username || "",
    password: snapshot?.password || "",
    model: snapshot?.model || "",
    modelDetails: snapshot?.model_details || null,

    // Behaviour
    description: snapshot.description || "",
    knowledgeBase: snapshot.knowledge_base || "",
    countryCode: matchedPin || "",
    contactNumber: localNumber,
    inbound: snapshot.inbound ?? true,
    commitMessage: snapshot.commit_message || "",

    // LiveKit fields
    livekitUrl: snapshot.livekit_url || "",
    livekitApiKey: snapshot.livekit_api_key || "",
    livekitApiSecret: snapshot.livekit_api_secret || "",
    livekitAgentName: snapshot.livekit_agent_name || "",
    livekitConfigJson: (() => {
      const val = snapshot.livekit_config_json;
      if (!val || (typeof val === "object" && Object.keys(val).length === 0))
        return "";
      if (typeof val === "string") return val;
      return JSON.stringify(val, null, 2);
    })(),
    livekitMaxConcurrency: snapshot.livekit_max_concurrency ?? 5,
  };
};
