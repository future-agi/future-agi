"""
Comprehensive test cases for ModelServingClient

Run with: python -m pytest agentic_eval/tests/test_serving_client.py -v
"""

import base64
from unittest.mock import Mock, patch

import pytest
import requests
from PIL import Image

from agentic_eval.core.embeddings.serving_client import (
    ModelServingClient,
    close_serving_client,
    get_serving_client,
)


@pytest.mark.unit
@pytest.mark.serving
@pytest.mark.embedding
class TestModelServingClient:
    """Test cases for ModelServingClient"""

    def setup_method(self):
        """Setup for each test method"""
        self.base_url = "http://test-serving:8080"
        self.client = ModelServingClient(base_url=self.base_url)

    def teardown_method(self):
        """Cleanup after each test"""
        if hasattr(self.client, 'session'):
            self.client.close()

    def test_initialization_default_url(self):
        """Test client initialization with default URL"""
        with patch.dict('os.environ', {'MODEL_SERVING_URL': 'http://env-serving:8080'}):
            client = ModelServingClient()
            assert client.base_url == 'http://env-serving:8080'
            client.close()

    def test_initialization_custom_url(self):
        """Test client initialization with custom URL"""
        custom_url = "http://custom-serving:9090"
        client = ModelServingClient(base_url=custom_url)
        assert client.base_url == custom_url
        client.close()

    def test_initialization_environment_config(self):
        """Test client initialization with environment variables"""
        env_vars = {
            'MODEL_SERVING_TIMEOUT': '60',
            'MODEL_SERVING_MAX_RETRIES': '5'
        }
        with patch.dict('os.environ', env_vars):
            client = ModelServingClient()
            assert client.default_timeout == 60
            assert client.max_retries == 5
            client.close()

    def test_session_creation(self):
        """Test that session is created with proper configuration"""
        assert self.client.session is not None
        assert isinstance(self.client.session, requests.Session)
        assert 'Content-Type' in self.client.session.headers
        assert self.client.session.headers['Content-Type'] == 'application/json'
        assert 'User-Agent' in self.client.session.headers

    @patch('requests.Session.post')
    def test_make_request_success(self, mock_post):
        """Test successful request"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embeddings": [0.1, 0.2, 0.3]}
        mock_post.return_value = mock_response

        result = self.client._make_request("/test", {"data": "test"})

        assert result == {"embeddings": [0.1, 0.2, 0.3]}
        mock_post.assert_called_once()

    @patch('requests.Session.post')
    def test_make_request_404_error(self, mock_post):
        """Test 404 error handling"""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Model not found"
        mock_post.return_value = mock_response

        with pytest.raises(ValueError, match="Model or endpoint not found"):
            self.client._make_request("/test", {"data": "test"})

    @patch('requests.Session.post')
    def test_make_request_400_error(self, mock_post):
        """Test 400 error handling"""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        mock_post.return_value = mock_response

        with pytest.raises(ValueError, match="Bad request"):
            self.client._make_request("/test", {"data": "test"})

    @patch('requests.Session.post')
    def test_make_request_timeout(self, mock_post):
        """Test timeout error handling"""
        mock_post.side_effect = requests.exceptions.Timeout()

        with pytest.raises(TimeoutError, match="Request timed out"):
            self.client._make_request("/test", {"data": "test"})

    @patch('requests.Session.post')
    def test_make_request_connection_error(self, mock_post):
        """Test connection error handling"""
        mock_post.side_effect = requests.exceptions.ConnectionError()

        with pytest.raises(ConnectionError, match="Failed to connect"):
            self.client._make_request("/test", {"data": "test"})

    def test_embed_text_string_input(self):
        """Test text embedding with string input"""
        with patch.object(self.client, '_make_request') as mock_request:
            mock_request.return_value = {"embeddings": [0.1, 0.2, 0.3]}

            result = self.client.embed_text("Hello world")

            assert result == [0.1, 0.2, 0.3]
            mock_request.assert_called_once_with("/embed", {
                "text": ["Hello world"],
                "input_type": "text"
            })

    def test_embed_text_list_input(self):
        """Test text embedding with list input"""
        with patch.object(self.client, '_make_request') as mock_request:
            mock_request.return_value = {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}

            result = self.client.embed_text(["Hello", "world"])

            assert result == [[0.1, 0.2], [0.3, 0.4]]
            mock_request.assert_called_once_with("/embed", {
                "text": ["Hello", "world"],
                "input_type": "text"
            })

    def test_embed_text_empty_input(self):
        """Test text embedding with empty input"""
        with pytest.raises(ValueError, match="Text input cannot be empty"):
            self.client.embed_text("")

        with pytest.raises(ValueError, match="Text input cannot be empty"):
            self.client.embed_text([])

    def test_embed_text_invalid_input(self):
        """Test text embedding with invalid input types"""
        # Test with None values in list
        with pytest.raises(ValueError, match="All text inputs must be strings"):
            self.client.embed_text(["Hello", "world", None])  # type: ignore

    def test_embed_image_string_input(self):
        """Test image embedding with string input"""
        with patch.object(self.client, '_make_request') as mock_request:
            with patch.object(self.client, '_process_image_input') as mock_process:
                mock_process.return_value = "processed_image"
                mock_request.return_value = {"embeddings": [0.1, 0.2, 0.3]}

                result = self.client.embed_image("http://example.com/image.jpg")

                assert result == [0.1, 0.2, 0.3]
                mock_process.assert_called_once_with("http://example.com/image.jpg")
                mock_request.assert_called_once_with("/embed/image", {
                    "image": "processed_image",
                    "input_type": "image"
                })

    def test_embed_image_none_input(self):
        """Test image embedding with None input"""
        # Use type: ignore to test runtime behavior while acknowledging type error
        with pytest.raises(ValueError, match="Image input cannot be None"):
            self.client.embed_image(None)  # type: ignore

    def test_embed_audio_success(self):
        """Test audio embedding"""
        with patch.object(self.client, '_make_request') as mock_request:
            with patch.object(self.client, '_process_audio_input') as mock_process:
                mock_process.return_value = "processed_audio"
                mock_request.return_value = {"embeddings": [0.1, 0.2, 0.3]}

                result = self.client.embed_audio(b"audio_bytes")

                assert result == [0.1, 0.2, 0.3]
                mock_process.assert_called_once_with(b"audio_bytes")

    def test_embed_audio_none_input(self):
        """Test audio embedding with None input"""
        # Use type: ignore to test runtime behavior while acknowledging type error
        with pytest.raises(ValueError, match="Audio input cannot be None"):
            self.client.embed_audio(None)  # type: ignore

    def test_embed_image_text_with_text(self):
        """Test image-text embedding with text input"""
        with patch.object(self.client, '_make_request') as mock_request:
            mock_request.return_value = {"embeddings": [0.1, 0.2, 0.3]}

            result = self.client.embed_image_text("Hello world")

            assert result == [0.1, 0.2, 0.3]
            mock_request.assert_called_once_with("/embed/image-text", {
                "text": "Hello world",
                "input_type": "image-text"
            })

    def test_embed_image_text_with_image(self):
        """Test image-text embedding with image input"""
        with patch.object(self.client, '_make_request') as mock_request:
            with patch.object(self.client, '_process_image_input') as mock_process:
                mock_process.return_value = "processed_image"
                mock_request.return_value = {"embeddings": [0.1, 0.2, 0.3]}

                # Create a simple PIL image
                img = Image.new('RGB', (10, 10), color='red')
                result = self.client.embed_image_text(img)

                assert result == [0.1, 0.2, 0.3]
                mock_process.assert_called_once_with(img)

    def test_embed_image_text_none_input(self):
        """Test image-text embedding with None input"""
        # Use type: ignore to test runtime behavior while acknowledging type error
        with pytest.raises(ValueError, match="Content input cannot be None"):
            self.client.embed_image_text(None)  # type: ignore

    def test_get_syn_data_embedding(self):
        """Test synthetic data embedding"""
        with patch('requests.Session.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"embeddings": [0.1, 0.2, 0.3]}
            mock_post.return_value = mock_response

            result = self.client.get_syn_data_embedding("test text")

            assert result == [0.1, 0.2, 0.3]
            mock_post.assert_called_once_with(
                f"{self.base_url}/model/v1/embed/syn-data",
                json={"text": "test text", "input_type": "text"},
                timeout=self.client.default_timeout
            )

    def test_process_image_input_string(self):
        """Test image input processing with string"""
        url = "http://example.com/image.jpg"
        result = self.client._process_image_input(url)
        assert result == url

    def test_process_image_input_pil_image(self):
        """Test image input processing with PIL Image"""
        img = Image.new('RGB', (10, 10), color='red')
        result = self.client._process_image_input(img)

        assert result.startswith("data:image/")
        assert "base64," in result

    def test_process_image_input_bytes(self):
        """Test image input processing with bytes"""
        img_bytes = b"fake_image_bytes"
        result = self.client._process_image_input(img_bytes)

        expected = f"data:image/jpeg;base64,{base64.b64encode(img_bytes).decode('utf-8')}"
        assert result == expected

    def test_process_image_input_invalid_type(self):
        """Test image input processing with invalid type"""
        # Use type: ignore to test runtime behavior while acknowledging type error
        with pytest.raises(ValueError, match="Unsupported image type"):
            self.client._process_image_input(123)  # type: ignore

    def test_process_audio_input_string(self):
        """Test audio input processing with string"""
        audio_data = "base64_audio_data"
        result = self.client._process_audio_input(audio_data)
        assert result == "data:audio/wav;base64,base64_audio_data"

    def test_process_audio_input_string_with_data_prefix(self):
        """Test audio input processing with data: prefix"""
        audio_data = "data:audio/wav;base64,audio_data"
        result = self.client._process_audio_input(audio_data)
        assert result == audio_data

    def test_process_audio_input_bytes(self):
        """Test audio input processing with bytes"""
        audio_bytes = b"fake_audio_bytes"
        result = self.client._process_audio_input(audio_bytes)

        expected = f"data:audio/wav;base64,{base64.b64encode(audio_bytes).decode('utf-8')}"
        assert result == expected

    def test_process_audio_input_invalid_type(self):
        """Test audio input processing with invalid type"""
        # Use type: ignore to test runtime behavior while acknowledging type error
        with pytest.raises(ValueError, match="Unsupported audio type"):
            self.client._process_audio_input(123)  # type: ignore

    @patch('requests.Session.get')
    def test_health_check_success(self, mock_get):
        """Test successful health check"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = self.client.health_check(use_cache=False)

        assert result is True
        mock_get.assert_called_once()

    @patch('requests.Session.get')
    def test_health_check_failure(self, mock_get):
        """Test failed health check"""
        mock_response = Mock()
        mock_response.status_code = 503
        mock_get.return_value = mock_response

        result = self.client.health_check(use_cache=False)

        assert result is False

    @patch('requests.Session.get')
    def test_health_check_exception(self, mock_get):
        """Test health check with exception"""
        mock_get.side_effect = requests.exceptions.ConnectionError()

        result = self.client.health_check(use_cache=False)

        assert result is False

    @pytest.mark.slow
    def test_health_check_caching(self):
        """Test health check caching functionality"""
        with patch('requests.Session.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            # First call
            result1 = self.client.health_check(use_cache=True)
            # Second call should use cache
            result2 = self.client.health_check(use_cache=True)

            assert result1 is True
            assert result2 is True
            # Should only call once due to caching
            assert mock_get.call_count == 1

    @patch('requests.Session.get')
    def test_get_model_status_success(self, mock_get):
        """Test successful model status retrieval"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": ["text_embedding", "image_embedding"]}
        mock_get.return_value = mock_response

        result = self.client.get_model_status()

        assert result == {"models": ["text_embedding", "image_embedding"]}
        mock_get.assert_called_once()

    @patch('requests.Session.get')
    def test_get_model_status_failure(self, mock_get):
        """Test model status retrieval failure"""
        mock_get.side_effect = requests.exceptions.ConnectionError()

        with pytest.raises(Exception):
            self.client.get_model_status()

    def test_close_session(self):
        """Test session closing"""
        mock_session = Mock()
        self.client.session = mock_session

        self.client.close()

        mock_session.close.assert_called_once()


@pytest.mark.unit
@pytest.mark.serving
class TestGlobalClientFunctions:
    """Test global client management functions"""

    def teardown_method(self):
        """Cleanup after each test"""
        close_serving_client()

    def test_get_serving_client_singleton(self):
        """Test that get_serving_client returns the same instance"""
        client1 = get_serving_client()
        client2 = get_serving_client()

        assert client1 is client2

    def test_close_serving_client(self):
        """Test closing the global client"""
        client = get_serving_client()
        with patch.object(client, 'close') as mock_close:
            close_serving_client()
            mock_close.assert_called_once()

    def test_close_serving_client_when_none(self):
        """Test closing when no client exists"""
        close_serving_client()  # Should not raise any errors


@pytest.mark.integration
@pytest.mark.serving
class TestErrorScenarios:
    """Test various error scenarios"""

    def setup_method(self):
        self.client = ModelServingClient(base_url="http://test-serving:8080")

    def teardown_method(self):
        self.client.close()

    def test_network_failures(self):
        """Test handling of various network failures"""
        with patch.object(self.client, '_make_request') as mock_request:
            # Test different network errors
            mock_request.side_effect = TimeoutError("Request timed out")
            with pytest.raises(TimeoutError):
                self.client.embed_text("test")

            mock_request.side_effect = ConnectionError("Connection failed")
            with pytest.raises(ConnectionError):
                self.client.embed_text("test")

    def test_invalid_server_responses(self):
        """Test handling of invalid server responses"""
        with patch.object(self.client, '_make_request') as mock_request:
            # Test malformed response
            mock_request.return_value = {"invalid": "response"}

            with pytest.raises(KeyError):
                self.client.embed_text("test")


@pytest.mark.performance
@pytest.mark.serving
class TestPerformance:
    """Basic performance testing"""

    def setup_method(self):
        self.client = ModelServingClient(base_url="http://test-serving:8080")

    def teardown_method(self):
        self.client.close()

    @patch('requests.Session.post')
    def test_batch_processing(self, mock_post):
        """Test batch processing performance"""
        mock_response = Mock()
        mock_response.status_code = 200
        # Create 100 individual embeddings, each as a list of 3 values
        # Wrap in extra array layer to work with the response processing logic
        embeddings = [[0.1, 0.2, 0.3] for _ in range(100)]
        mock_response.json.return_value = {"embeddings": [embeddings]}
        mock_post.return_value = mock_response

        # Test large batch
        large_batch = [f"Text {i}" for i in range(100)]
        result = self.client.embed_text(large_batch)

        assert len(result) == 100
        mock_post.assert_called_once()


# Test fixtures and helpers
@pytest.fixture
def mock_serving_response():
    """Fixture for mock serving response"""
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"embeddings": [0.1, 0.2, 0.3]}
    return response


@pytest.fixture
def sample_image():
    """Fixture for sample PIL image"""
    return Image.new('RGB', (10, 10), color='red')


@pytest.fixture
def sample_audio_bytes():
    """Fixture for sample audio bytes"""
    return b"fake_audio_data"


@pytest.mark.integration
def test_usage_example(mock_serving_response):
    """Test typical usage patterns"""
    with patch('requests.Session.post', return_value=mock_serving_response):
        client = ModelServingClient()

        # Test typical usage
        text_embeddings = client.embed_text("Hello world")
        batch_embeddings = client.embed_text(["Hello", "world"])

        assert text_embeddings == [0.1, 0.2, 0.3]
        assert batch_embeddings == [0.1, 0.2, 0.3]

        client.close()


if __name__ == "__main__":
    # Run with: python -m pytest agentic_eval/tests/test_serving_client.py -v
    import sys

    print("🧪 Running ModelServingClient Tests")
    print("=" * 50)

    # Run the tests
    exit_code = pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "--durations=10",  # Show 10 slowest tests
        "-x"  # Stop on first failure
    ])

    sys.exit(exit_code)
