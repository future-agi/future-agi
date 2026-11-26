import threading

from agentic_eval.core.embeddings.embedding_manager import model_manager

_instance_lock = threading.Lock()
_client_instance_lock = threading.Lock()


def get_embedding_model(input_type="text"):
    """
    Returns an instance of the embedding model based on the input type.

    Args:
        input_type (str): The type of embedding model to use, either "text" or "image".
                          Defaults to "text".

    Returns:
        EmbeddingModelLocal: An instance of the embedding model based on the input type.
    """
    if input_type == "text":
        return model_manager.text_model
    elif input_type == "image":
        return model_manager.image_model
    elif input_type == "image-text":
        return model_manager.image_text_model
    elif input_type == "check_serving":
        return model_manager._use_serving
    else:
        raise ValueError(
            "Invalid input type. Expected 'text', 'image', or 'image-text'."
        )
