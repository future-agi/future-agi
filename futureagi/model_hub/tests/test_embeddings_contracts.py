import pytest
from rest_framework import status


def _result(response):
    payload = response.json()
    return payload.get("result", payload)


@pytest.mark.django_db
def test_embedding_catalog_lists_static_providers(auth_client):
    response = auth_client.get("/model-hub/embeddings/")

    assert response.status_code == status.HTTP_200_OK
    embeddings = _result(response)["embeddings"]
    assert set(embeddings) == {"openai", "huggingface", "sentence_transformers"}
    assert embeddings["openai"]["config_schema"]["model"]["default"] == (
        "text-embedding-ada-002"
    )
    assert embeddings["sentence_transformers"]["requires_api_key"] is False


@pytest.mark.django_db
def test_embedding_detail_route_uses_path_provider_type(auth_client):
    response = auth_client.get("/model-hub/embeddings/openai/")

    assert response.status_code == status.HTTP_200_OK
    result = _result(response)
    assert "embedding" in result
    assert "embeddings" not in result
    assert result["embedding"]["name"] == "OpenAI Embeddings"
    assert result["embedding"]["requires_api_key"] is True


@pytest.mark.django_db
def test_embedding_detail_route_rejects_unknown_path_type(auth_client):
    response = auth_client.get("/model-hub/embeddings/not-a-provider/")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid embedding type: not-a-provider" in str(response.json())


@pytest.mark.django_db
def test_embedding_detail_query_parameter_remains_supported(auth_client):
    response = auth_client.get(
        "/model-hub/embeddings/",
        {"type": "sentence_transformers"},
    )

    assert response.status_code == status.HTTP_200_OK
    result = _result(response)
    assert "embedding" in result
    assert result["embedding"]["requires_api_key"] is False
