const FRAMEWORK_LABELS = {
  langchain: "LangChain",
  langgraph: "LangGraph",
  livekit: "LiveKit Agents",
  claude_agent_sdk: "Claude Agent SDK",
  openai_agents: "OpenAI Agents SDK",
  pydantic_ai: "Pydantic AI",
  google_adk: "Google ADK",
  crewai: "CrewAI",
  pipecat: "Pipecat",
  deepagents: "Deep Agents",
  none: "No framework",
  unknown: "Framework not detected",
};

export const frameworkLabel = (framework) => {
  if (!framework) return "Framework not detected";
  if (framework.name === "other") return framework.label || "Other framework";
  const name = FRAMEWORK_LABELS[framework.name] || framework.name;
  return framework.version ? `${name} ${framework.version}` : name;
};
