import PropTypes from "prop-types";
import { AGENT_TYPES } from "src/sections/agents/constants";
import PanelHostedPlatform from "../../panels/PanelHostedPlatform";

const PLATFORM_BY_TYPE = {
  vapi: { [AGENT_TYPES.VOICE]: "vapi" },
  retell: { [AGENT_TYPES.VOICE]: "retell", [AGENT_TYPES.CHAT]: "retell_chat" },
  livekit: { [AGENT_TYPES.VOICE]: "livekit" },
  phone: { [AGENT_TYPES.VOICE]: "other" },
};

export default function HostedPlatformForm({ item, onBusyChange }) {
  return (
    <PanelHostedPlatform
      key={item?.id}
      platformByType={PLATFORM_BY_TYPE[item?.id]}
      onBusyChange={onBusyChange}
    />
  );
}

HostedPlatformForm.propTypes = {
  item: PropTypes.object,
  onBusyChange: PropTypes.func,
};
