import re
from typing import Any

from langchain_community.chat_models import (
    AzureChatOpenAI,
    BedrockChat,
    ChatAnthropic,
    ChatAnyscale,
    ChatBaichuan,
    ChatDatabricks,
    ChatDeepInfra,
    ChatEverlyAI,
    ChatFireworks,
    ChatGooglePalm,
    ChatHuggingFace,
    ChatHunyuan,
    ChatJavelinAIGateway,
    ChatKonko,
    ChatLiteLLM,
    ChatLiteLLMRouter,
    ChatMlflow,
    ChatMLflowAIGateway,
    ChatOllama,
    ChatOpenAI,
    ChatSparkLLM,
    ChatVertexAI,
    ChatYandexGPT,
    ChatZhipuAI,
    ErnieBotChat,
    FakeListChatModel,
    GigaChat,
    GPTRouter,
    HumanInputChatModel,
    JinaChat,
    LlamaEdgeChatService,
    MiniMaxChat,
    PaiEasChatEndpoint,
    PromptLayerChatOpenAI,
    QianfanChatEndpoint,
    VolcEngineMaasChat,
)
from langchain_community.llms.anthropic import Anthropic
from langchain_community.llms.bedrock import Bedrock
from langchain_community.llms.openai import AzureOpenAI, OpenAI
from langchain_core.load import dumps, loads

# NOTE ON DEPENDENCIES:
# - langchain-openai is a separate package and imports openai models. Need to match these models manually
# - langchain_community is loaded as a dependency of langchain, so we can use it here


def _extract_model_name(
    serialized: dict[str, Any],
    **kwargs: Any,
):
    """
    Extracts the model name from the serialized or kwargs object.
    """
    # we have to deal with ChatGoogleGenerativeAI and ChatMistralAI first, as
    # if we run loads(dumps(serialized)) on it, it will throw in case of missing api keys

    model = _extract_model_by_key(
        "ChatGoogleGenerativeAI",
        serialized,
        serialized,
        ["kwargs", "model"],
    )
    if model:
        return model

    model = _extract_model_by_key(
        "ChatMistralAI",
        serialized,
        serialized,
        ["kwargs", "model"],
    )
    if model:
        return model

    # checks if serializations is implemented. Otherwise, this will throw
    if serialized.get("type") != "not_implemented":
        try:
            # try to deserialize the model name from the serialized object
            llm = loads(dumps(serialized))

            # openai models from langchain_openai, separate package, not installed with langchain

            # community models from langchain_community, separate package, installed with langchain
            if isinstance(llm, ChatAnthropic):
                return llm.model

            if isinstance(llm, Anthropic):
                return llm.model

            if isinstance(llm, ChatAnyscale):
                return llm.model_name

            # openai community models
            if isinstance(llm, AzureChatOpenAI):
                return llm.model_name

            if isinstance(llm, ChatOpenAI):
                return llm.model_name

            if isinstance(llm, OpenAI):
                return llm.model_name

            if isinstance(llm, AzureOpenAI):
                invocation_params = kwargs.get("invocation_params") or {}
                model = invocation_params.get("model")
                if model:
                    return model + "-" + llm.serialized["kwargs"]["model_version"]

            if isinstance(llm, ChatBaichuan):
                return llm.model

            if isinstance(llm, QianfanChatEndpoint):
                return llm.model

            if isinstance(llm, BedrockChat):
                return llm.model_id

            if isinstance(llm, Bedrock):
                return llm.model_id

            if isinstance(llm, ChatDatabricks):
                return llm.name

            if isinstance(llm, ChatDeepInfra):
                return llm.model_name

            if isinstance(llm, ErnieBotChat):
                return llm.model_name

            if isinstance(llm, ChatEverlyAI):
                return llm.model_name

            if isinstance(llm, FakeListChatModel):
                return None

            if isinstance(llm, ChatFireworks):
                return llm.model

            if isinstance(llm, GigaChat):
                return llm.model

            if isinstance(llm, ChatGooglePalm):
                return llm.model_name

            if isinstance(llm, GPTRouter):
                # taking the last model from the priority list
                return (
                    llm.models_priority_list[-1].name
                    if len(llm.models_priority_list) > 0
                    else None
                )

            if isinstance(llm, ChatHuggingFace):
                return llm.model_id

            if isinstance(llm, HumanInputChatModel):
                return llm.name

            if isinstance(llm, ChatHunyuan):
                return llm.name

            if isinstance(llm, ChatJavelinAIGateway):
                return llm.name

            if isinstance(llm, JinaChat):
                return None

            if isinstance(llm, ChatKonko):
                return llm.model

            if isinstance(llm, ChatLiteLLM):
                return llm.model_name

            if isinstance(llm, ChatLiteLLMRouter):
                return llm.model_name

            if isinstance(llm, LlamaEdgeChatService):
                return llm.model

            if isinstance(llm, MiniMaxChat):
                return llm.model

            if isinstance(llm, ChatMlflow):
                return None

            if isinstance(llm, ChatMLflowAIGateway):
                return None

            if isinstance(llm, ChatOllama):
                return llm.model

            if isinstance(llm, PaiEasChatEndpoint):
                return None

            if isinstance(llm, PromptLayerChatOpenAI):
                return None

            if isinstance(llm, ChatSparkLLM):
                return None

            if isinstance(llm, ChatVertexAI):
                return llm.model_name

            if isinstance(llm, VolcEngineMaasChat):
                return llm.model

            if isinstance(llm, ChatYandexGPT):
                return llm.model_name

            if isinstance(llm, ChatZhipuAI):
                return llm.model
        except Exception:
            # using a try .. except block to catch exceptions if the model load above fails as some library is not installed for example
            pass
    # try to extract the model manually

    model = _extract_model_by_key(
        "ChatVertexAI",
        serialized,
        serialized,
        ["kwargs", "model_name"],
    )
    if model:
        return model

    # openai new langchain-openai package
    model = _extract_model_by_key(
        "OpenAI",
        serialized,
        kwargs,
        ["invocation_params", "model_name"],
    )
    if model:
        return model

    model = _extract_model_by_key(
        "ChatOpenAI",
        serialized,
        kwargs,
        ["invocation_params", "model_name"],
    )
    if model:
        return model

    model = _extract_model_by_key(
        "AzureChatOpenAI",
        serialized,
        kwargs,
        ["invocation_params", "model"],
    )
    if model:
        return model

    if serialized.get("id")[-1] == "AzureChatOpenAI":
        invocation_params = kwargs.get("invocation_params") or {}
        if invocation_params.get("model"):
            return invocation_params.get("model")

    if serialized.get("id")[-1] == "AzureOpenAI":
        invocation_params = kwargs.get("invocation_params") or {}
        if invocation_params.get("model_name"):
            return invocation_params.get("model_name")

        deployment_name = None
        if serialized.get("kwargs").get("openai_api_version"):
            deployment_name = serialized.get("kwargs").get("deployment_version")
        deployment_version = None
        if serialized.get("kwargs").get("deployment_name"):
            deployment_name = serialized.get("kwargs").get("deployment_name")
        return deployment_name + "-" + deployment_version

    # anthropic
    model = _extract_model_by_pattern("Anthropic", serialized, "model", "anthropic")
    if model:
        return model

    # anthropic
    model = _extract_model_by_pattern("ChatAnthropic", serialized, "model")
    if model:
        return model

    # chatongyi
    model = _extract_model_by_pattern("ChatTongyi", serialized, "model_name")
    if model:
        return model

    # Cohere
    model = _extract_model_by_pattern("ChatCohere", serialized, "model")
    if model:
        return model
    model = _extract_model_by_pattern("Cohere", serialized, "model")
    if model:
        return model

    # huggingface
    model = _extract_model_by_pattern("HuggingFaceHub", serialized, "model")
    if model:
        return model

    # anyscale
    model = _extract_model_by_pattern("ChatAnyscale", serialized, "model_name")
    if model:
        return model

    model = _extract_model_by_key(
        "HuggingFacePipeline",
        serialized,
        kwargs,
        ["invocation_params", "model_id"],
    )
    if model:
        return model

    # textgen
    model = _extract_model_by_pattern("TextGen", serialized, "model", "text-gen")
    if model:
        return model

    return None


def _extract_model_with_regex(pattern: str, text: str):
    match = re.search(rf"{pattern}='(.*?)'", text)
    if match:
        return match.group(1)
    return None


def _extract_model_by_pattern(
    id: str, serialized: dict, pattern: str, default: str | None = None
):
    if serialized.get("id")[-1] == id:
        extracted = _extract_model_with_regex(pattern, serialized["repr"])
        return extracted if extracted else default if default else None


def _extract_model_by_key(
    id: str,
    serialized: dict,
    object: dict,
    keys: list[str],
    default: str | None = None,
):
    if serialized.get("id")[-1] == id:
        current_obj = object
        for key in keys:
            current_obj = current_obj.get(key)
            if not current_obj:
                raise ValueError(f"Key {key} not found in {object}")

        return current_obj if current_obj else default if default else None
