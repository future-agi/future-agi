from typing import Any

import openai
import structlog
from app.servable_models.constants import trust_remote_code_enabled
from sentence_transformers import SentenceTransformer

from .base import ModelServing

logger = structlog.get_logger(__name__)


class OpenAIEmbeddingModel(ModelServing):
    """
    A wrapper for OpenAI's embedding models.
    """

    def __init__(self, model: str, api_key: str, **kwargs):
        super().__init__()  # type: ignore[safe-super]
        self.model_name = model
        self.api_key = api_key
        try:
            self.client = openai.OpenAI(api_key=self.api_key)
            logger.info(f"Initialized OpenAI client for model: {model}")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            raise

    def preprocess(self, data: list[str]) -> list[str]:
        """
        No preprocessing needed for OpenAI text.
        """
        return data

    def forward(self, data: list[str]) -> list[list[float]]:
        """
        Generate embeddings using the OpenAI API.
        """
        try:
            response = self.client.embeddings.create(model=self.model_name, input=data)
            embeddings = [item.embedding for item in response.data]
            return embeddings
        except Exception as e:
            logger.error(f"Error during OpenAI inference: {e}")
            raise

    def postprocess(self, output: Any) -> Any:
        """
        No postprocessing needed for the embeddings.
        """
        return output


class HuggingFaceEmbeddingModel(ModelServing):
    """
    A wrapper for Hugging Face's sentence-transformer models.
    """

    def __init__(self, model: str, **kwargs):
        super().__init__()  # type: ignore[safe-super]
        self.model_name = model
        try:
            self.model = SentenceTransformer(
                self.model_name,
                trust_remote_code=trust_remote_code_enabled(),
            )
            logger.info(f"Loaded HuggingFace model: {self.model_name}")
        except Exception as e:
            logger.error(f"Failed to load HuggingFace model {self.model_name}: {e}")
            raise

    def preprocess(self, data: list[str]) -> list[str]:
        """
        No preprocessing needed for sentence-transformer text.
        """
        return data

    def forward(self, data: list[str]) -> list[list[float]]:
        """
        Generate embeddings using a local SentenceTransformer model.
        """
        try:
            embeddings = self.model.encode(data, convert_to_tensor=False)
            return embeddings
        except Exception as e:
            logger.error(f"Error during HuggingFace inference: {e}")
            raise

    def postprocess(self, output: Any) -> Any:
        """
        No postprocessing needed for the embeddings.
        """
        return output
