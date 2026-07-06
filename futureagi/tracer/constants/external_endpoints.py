from enum import Enum


class ObservabilityRoutes(str, Enum):
    VAPI_CALL_URL = "https://api.vapi.ai/call"
    RETELL_LIST_CALLS_URL = "https://api.retellai.com/v3/list-calls"
    ELEVEN_LABS_CONVERSATIONS_URL = "https://api.elevenlabs.io/v1/convai/conversations"
    BLAND_CALLS_URL = "https://api.bland.ai/v1/calls"
    # {account_sid} interpolated at fetch time (Twilio's API is account-scoped).
    TWILIO_CALLS_URL = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"

    ## Assistant endpoints
    VAPI_ASSISTANT_URL = "https://api.vapi.ai/assistant"
    RETELL_GET_ASSISTANT_URL = "https://api.retellai.com/get-agent"
    RETELL_LIST_ASSISTANTS_URL = "https://api.retellai.com/list-agents"
