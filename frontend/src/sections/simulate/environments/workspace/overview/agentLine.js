import { AGENT_SUMMARY_COPY } from "./overview.constants";

const CODE_SOURCES = new Set(["github", "archive"]);

const CODE_ICON = {
  github: "simple-icons:github",
  archive: "solar:folder-with-files-linear",
};

const PLATFORM = {
  vapi: { label: "Vapi", logo: "vapi" },
  retell: { label: "Retell", logo: "retell" },
  retell_chat: { label: "Retell chat", logo: "retell_chat" },
  livekit: { label: "LiveKit", logo: "livekit" },
  phone: { label: "Phone number", icon: "solar:phone-linear" },
  foundry: { label: "Azure AI Foundry", icon: "solar:cpu-bolt-linear" },
  copilot_studio: { label: "Copilot Studio", icon: "solar:cpu-bolt-linear" },
};

export const isCodeSource = (source) => CODE_SOURCES.has(source?.kind);

export function agentLineOf({ subject, source, connector, policy, noWorld }) {
  const code = isCodeSource(source);
  const platform = code ? null : PLATFORM[connector] || PLATFORM[source?.kind];
  const version = subject?.active_version;
  const commit =
    source?.kind === "github"
      ? AGENT_SUMMARY_COPY.commitAt(source.ref, source.commit_sha)
      : "";
  const where = code
    ? AGENT_SUMMARY_COPY.sourceKind[source.kind]
    : platform?.label || null;
  return {
    logo: platform?.logo || null,
    icon: code
      ? CODE_ICON[source.kind]
      : platform?.icon || "solar:cpu-bolt-linear",
    label: where,
    title: subject
      ? [AGENT_SUMMARY_COPY.agentTitle(subject.name, version), commit]
          .filter(Boolean)
          .join(" · ")
      : AGENT_SUMMARY_COPY.noAgentTitle,
    facts: subject
      ? [
          where,
          noWorld
            ? AGENT_SUMMARY_COPY.connectOnly
            : AGENT_SUMMARY_COPY.simulated,
          policy === "pinned" && version
            ? AGENT_SUMMARY_COPY.defaultPinned(version)
            : AGENT_SUMMARY_COPY.defaultProduction,
        ]
          .filter(Boolean)
          .join(" · ")
      : AGENT_SUMMARY_COPY.portableBody,
  };
}
