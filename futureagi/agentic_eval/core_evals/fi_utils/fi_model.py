from enum import Enum


class Model(Enum):
    """
    Supported models for evaluations.
    """

    GPT35_TURBO = "gpt-3.5-turbo"
    GPT35_TURBO_1106 = "gpt-3.5-turbo-1106"
    GPT4 = "gpt-4"
    GPT4_O = "gpt-4o"
    GPT4_O_AUDIO = "gpt-4o-audio-preview"
    GPT4_32K = "gpt-4-32k"
    GPT4_1106_PREVIEW = "gpt-4-1106-preview"
    GPT4_TURBO_PREVIEW = "gpt-4-turbo-preview"
    GPT4_TURBO = "gpt-4-turbo"
    GPT35_TURBO_0125 = "gpt-3.5-turbo-0125"
    GPT35_TURBO_16K = "gpt-3.5-turbo-16k"
    COMMAND_LIGHT = "command-light"
    COMMAND = "command"
    COMMAND_R = "command-r"
    COMMAND_R_PLUS = "command-r-plus"
    AZURE_GPT35_TURBO = "azure/gpt-3.5-turbo"
    AZURE_GPT35_TURBO_1106 = "azure/gpt-3.5-turbo-1106"
    AZURE_GPT4 = "azure/gpt-4"
    AZURE_GPT4_1106_PREVIEW = "azure/gpt-4-1106-preview"
    GEMINI_PROD = "gemini/gemini-prod"
    GEMINI_PRO = "gemini/gemini-pro"
    GEMINI_15_PRO_LATEST = "gemini/gemini-1.5-pro-latest"
    CLAUDE_2 = "claude-2"
    CLAUDE_21 = "claude-2.1"
    CLAUDE_3_HAIKU_20240307 = "claude-3-haiku-20240307"
    CLAUDE_3_SONNET_20240229 = "claude-3-sonnet-20240229"
    CLAUDE_3_OPUS_20240229 = "claude-3-opus-20240229"
    MISTRAL_TINY = "mistral/mistral-tiny"
    MISTRAL_SMALL = "mistral/mistral-small"
    MISTRAL_MEDIUM = "mistral/mistral-medium"
    MISTRAL_LARGE = "mistral/mistral-large-latest"
    GROQ_LLAMA3_8B_8192 = "groq/llama3-8b-8192"
    GROQ_LLAMA3_70B_8192 = "groq/llama3-70b-8192"
    HUGGINGFACE_META_LLAMA_3_8B = "huggingface/meta-llama/meta-llama-3-8b"
    HUGGINGFACE_META_LLAMA_3_70B = "huggingface/meta-llama/meta-llama-3-70b"

    @staticmethod
    def is_supported(model_name: str) -> bool:
        """
        Checks if the model is supported.
        """
        return model_name in [model.value for model in Model]

    @staticmethod
    def supports_json_mode(model_name: str) -> bool:
        """
        Checks if the model supports json mode.
        """
        JSON_MODE_SUPPORTED_MODELS = [Model.GPT4_1106_PREVIEW, Model.GPT35_TURBO_1106]
        return model_name in [model.value for model in JSON_MODE_SUPPORTED_MODELS]
