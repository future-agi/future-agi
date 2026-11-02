import { createContext, useContext } from "react";

/*
  Connecting a new agent from inside the Build environment drawer, as in Vel's
  agent picker. The drawer opens the ways to connect as a layer of its own and
  keeps the agents connected that way (samples, since connecting here isn't
  wired to the API yet) for the pickers to offer.
*/

/** `{ requestConnect({ modality, onConnected }), samples }`, or null outside the drawer. */
export const ConnectNewAgentContext = createContext(null);
export const useConnectNewAgent = () => useContext(ConnectNewAgentContext);

/** `{ item, modality, onConnected }` around a way's form opened to connect an agent. */
export const ConnectModeContext = createContext(null);
export const useConnectMode = () => useContext(ConnectModeContext);

const TYPE_WORD = { voice: "voice", text: "chat" };

/** "Connect a voice agent", or "Connect a new agent" for any type. */
export const connectTitleFor = (modality) =>
  TYPE_WORD[modality]
    ? `Connect a ${TYPE_WORD[modality]} agent`
    : "Connect a new agent";

export const connectSubtitleFor = (modality) => {
  const word = TYPE_WORD[modality];
  return word
    ? `This world is for ${word} agents, so only ways to connect a ${word} agent are shown. It joins Agents, and this environment is built for it.`
    : "It joins Agents, and this environment is built for it.";
};

/** The sample agent a way's connect form adds in place of a real one. */
export const sampleAgentFor = (item, modality) => ({
  id: `sample-${Date.now()}`,
  name: `${item.name} agent`,
  modality,
  source: item.id,
  latestLabel: "v1",
  environmentCount: 0,
  sample: true,
});
