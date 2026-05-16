from typing import Any

import requests

from ..clients.constants import MODEL_SERVING_BASE_URL

_HTTP_REQUEST_TIMEOUT_SECONDS = 30


class ModelServingClient:
    _instance = None

    def __new__(cls, base_url: str = MODEL_SERVING_BASE_URL):
        """
        Creates a new instance of ModelServingClient if none exists, otherwise returns the existing instance.

        Args:
            base_url (str): The base URL of the server to send requests to. This should be provided only
                            when creating the instance for the first time.

        Returns:
            ModelServingClient: The singleton instance of the class.
        """
        if cls._instance is None:
            if base_url is None:
                raise ValueError("base_url must be provided for the first instance creation.")
            _instance = super().__new__(cls)
            _instance.base_url = base_url
            cls._instance = _instance
        return cls._instance

    def get(self, endpoint: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """
        Sends a GET request to the specified endpoint.

        Args:
            endpoint (str): The endpoint to send the GET request to.
            params (Union[Dict[str, Any], None]): Optional query parameters to include in the request.
            headers (Dict[str, str]): Optional headers to include in the request.

        Returns:
            Dict[str, Any]: The JSON response from the server as a dictionary.

        Raises:
            requests.RequestException: If there is an issue with the request.
            requests.HTTPError: If the server responds with a non-2xx status code.
        """
        try:
            response = requests.get(
                f"{self.base_url}/{endpoint}",
                params=params,
                headers=headers,
                timeout=_HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()  # Raise an exception for HTTP errors
            return response.json()  # Return the JSON response as a dictionary
        except requests.RequestException as exc:
            raise RuntimeError(f"An error occurred while requesting {exc.request.url!r}.") from exc

    def post(self, endpoint: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """
        Sends a POST request to the specified endpoint.

        Args:
            endpoint (str): The endpoint to send the POST request to.
            json (Union[Dict[str, Any], None]): Optional JSON data to include in the request body.
            headers (Dict[str, str]): Optional headers to include in the request.

        Returns:
            Dict[str, Any]: The JSON response from the server as a dictionary.

        Raises:
            requests.RequestException: If there is an issue with the request.
            requests.HTTPError: If the server responds with a non-2xx status code.
        """
        try:
            response = requests.post(
                f"{self.base_url}/{endpoint}",
                json=json,
                headers=headers,
                timeout=_HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()  # Raise an exception for HTTP errors
            return response.json()  # Return the JSON response as a dictionary
        except requests.RequestException as exc:
            raise RuntimeError(f"An error occurred while requesting {exc.request.url!r}.")
