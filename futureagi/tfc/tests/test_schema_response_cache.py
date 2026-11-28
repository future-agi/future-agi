import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator

from accounts.authentication import APIKeyAuthentication


@pytest.mark.parametrize(
    ("path", "media_type"),
    [
        ("/swagger.json", "application/json"),
        ("/swagger.yaml", "application/yaml"),
        ("/docs/?format=openapi", "application/openapi+json"),
        ("/docs/", "application/openapi+json"),
    ],
)
def test_swagger_response_cache(client, path, media_type):
    client.defaults["HTTP_HOST"] = f"{uuid4().hex}.example.test"
    schema = openapi.Swagger(info=openapi.Info("Reference", "v1"), _prefix="/")
    with patch.object(
        OpenAPISchemaGenerator, "get_schema", return_value=schema
    ) as build:
        if path == "/docs/":
            html = client.get(path, HTTP_ACCEPT="text/html")
            assert html["Content-Type"].startswith("text/html")
            build.reset_mock()

        cold = client.get(path, HTTP_ACCEPT=media_type)
        warm = client.get(path, HTTP_ACCEPT=media_type)
        assert cold.status_code == warm.status_code == 200
        assert cold["Content-Type"].split(";")[0] == media_type
        assert cold.content == warm.content
        build.assert_called_once()

        schema.info.version = "v2"
        with patch("time.time", return_value=time.time() + 24 * 60 * 60 + 1):
            refreshed = client.get(path, HTTP_ACCEPT=media_type)
        assert refreshed.status_code == 200
        assert build.call_count == 2
        assert refreshed.content != cold.content

        with patch.object(
            APIKeyAuthentication, "authenticate", return_value=None
        ) as auth:
            client.get(path, HTTP_ACCEPT=media_type, HTTP_X_API_KEY="docs-client")
        auth.assert_called_once()
