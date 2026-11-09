from app.servable_models import EmbeddingModel, ModelServing
from app.servable_models.embedding_models import (
    AudioEmbeddingModel,
    ImageEmbeddingModel,
    ImageTextEmbeddingModel,
    SynDataEmbeddingModel,
    TextEmbeddingModel,
)
from app.servable_models.external_models import (
    HuggingFaceEmbeddingModel,
    OpenAIEmbeddingModel,
)

SUPPORTED_MODELS = [
    "embedding",
    "text_embedding",
    "image_embedding",
    "audio_embedding",
    "image_text_embedding",
    "syn_data_embedding",
    "openai",
    "huggingface",
]

# Server-owned allowlist for the customer-selectable "huggingface" loader
# (POST /model/v1/infer/huggingface, model_params["model"]). Exact strings
# only: anything else, including every local or cache path, is refused in
# app.v1.endpoints before a loader runs. Never extend this from a request.
# Mirrored for request validation in
# model_hub/serializers/contracts.py APPROVED_HUGGINGFACE_EMBEDDING_MODELS.
APPROVED_HUGGINGFACE_MODELS = frozenset(
    {
        # The backend default for embedding_config type "huggingface" and
        # "sentence_transformers": model_hub/views/dynamic_columns.py
        # _query_vector_db (and the develop_dataset.py copy), and the
        # "sentence_transformers" default in the embedding catalog
        # (model_hub/views/develop_dataset.py GetEmbeddingsListView).
        "all-mpnet-base-v2",
        # The same repo by its full Hub ID: the "huggingface" default in
        # that catalog.
        "sentence-transformers/all-mpnet-base-v2",
        # This service's own text_embedding / syn_data_embedding model,
        # already loaded without remote code: constants.py
        # MODEL_NAME_TO_SENTENCE_TRANSFORMER_MODEL_MAPPING,
        # embedding_models.py TextEmbeddingModel / SynDataEmbeddingModel.
        "all-MiniLM-L6-v2",
        # The same repo by its full Hub ID (sentence-transformers resolves
        # the bare name to it).
        "sentence-transformers/all-MiniLM-L6-v2",
    }
)

MODEL_NAME_TO_CLASS_MAPPING = {
    "embedding": EmbeddingModel,
    "text_embedding": TextEmbeddingModel,
    "image_embedding": ImageEmbeddingModel,
    "audio_embedding": AudioEmbeddingModel,
    "image_text_embedding": ImageTextEmbeddingModel,
    "syn_data_embedding": SynDataEmbeddingModel,
    "openai": OpenAIEmbeddingModel,
    "huggingface": HuggingFaceEmbeddingModel,
}
