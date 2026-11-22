"""The values the agent list filters accept, apart from the queries they run."""

AGENT_SOURCE_VALUES = {
    "github": ("github",),
    "archive": ("archive",),
    "livekit": ("livekit", "livekit_bridge"),
    "vapi": ("vapi",),
    "retell": ("retell",),
    "bland": ("bland",),
    "eleven_labs": ("eleven_labs",),
}
AGENT_SOURCE_FILTERS = tuple(AGENT_SOURCE_VALUES)
AGENT_ENVIRONMENT_FILTERS = ("with", "without")
AGENT_OBSERVE_FILTERS = ("linked", "not_linked")
