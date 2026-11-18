import PropTypes from "prop-types";
import { AGENT_TYPES } from "src/sections/agents/constants";
import PanelHostedPlatform from "../../panels/PanelHostedPlatform";

const PRESETS = {
  vapi: { agentType: AGENT_TYPES.VOICE, platform: "vapi" },
  retell: { agentType: AGENT_TYPES.VOICE, platform: "retell" },
  livekit: { agentType: AGENT_TYPES.VOICE, platform: "livekit" },
  phone: { agentType: AGENT_TYPES.VOICE, platform: "other" },
};

export default function HostedPlatformForm({ item }) {
  return <PanelHostedPlatform key={item?.id} preset={PRESETS[item?.id]} />;
}

HostedPlatformForm.propTypes = { item: PropTypes.object };
