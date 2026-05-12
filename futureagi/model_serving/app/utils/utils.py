import time
from io import BytesIO

import av
import requests
import structlog

logger = structlog.get_logger(__name__)


def download_audio_from_url(audio_url, max_retries=5, timeout=200):
    # PYTHONWARNINGS="error::AssertionError"
    """
    Downloads an audio file from the provided URL with retries and error handling.
    Processes audio data in memory and converts to MP3 format if needed.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(audio_url, timeout=timeout, headers=headers)
            if response.status_code == 200:
                # Get the content directly
                audio_data = response.content

                # Check if the file is already in MP3 format
                is_mp3 = False
                try:
                    # Create a temporary file-like object in memory
                    audio_input = BytesIO(audio_data)
                    # Check if it's already an MP3 file
                    if (
                        audio_url.lower().endswith(".mp3")
                        or response.headers.get("Content-Type", "").lower()
                        == "audio/mpeg"
                    ):
                        # Verify it's a valid MP3 file
                        try:
                            audio_input.seek(0)  # Reset position

                            # Use PyAV to check format
                            container = av.open(audio_input)

                            # Check if format is MP3
                            is_mp3 = container.format.name == "mp3" or (
                                container.streams.audio
                                and container.streams.audio[0].codec_context.name
                                in ["mp3", "mp3float"]
                            )

                            container.close()
                            audio_input.seek(0)  # Reset for later use

                            if is_mp3:
                                logger.debug(
                                    "Audio is already in MP3 format, no conversion needed"
                                )

                        except Exception:
                            is_mp3 = False
                except Exception as e:
                    logger.error(f"Error checking audio format: {e}")
                    is_mp3 = False

                # If it's already MP3, return the original

                if is_mp3:
                    return audio_data

                # Convert the audio to MP3 format
                try:
                    # Reset the file pointer
                    # with gsub.patch():
                    # Use PyAV to convert to MP3
                    audio_input = BytesIO(audio_data)
                    audio_output = BytesIO()

                    # PyAV replacement for pydub
                    input_container = av.open(audio_input)
                    output_container = av.open(audio_output, mode="w", format="mp3")

                    # Get the first audio stream
                    input_stream = input_container.streams.audio[0]

                    # Create output stream - let PyAV handle the codec configuration
                    output_stream = output_container.add_stream(
                        "mp3", rate=input_stream.rate
                    )

                    # Decode and re-encode
                    for packet in input_container.demux(input_stream):
                        for frame in packet.decode():
                            for packet in output_stream.encode(frame):
                                output_container.mux(packet)

                    # Flush encoder
                    for packet in output_stream.encode():
                        output_container.mux(packet)

                    # Close containers
                    output_container.close()
                    input_container.close()

                    # Get the converted data
                    converted_data = audio_output.getvalue()
                    return converted_data
                except Exception as e:
                    logger.error(
                        f"Error converting audio (download_audio_from_url) : {e}"
                    )
                    # If conversion fails, return the original data
                    return audio_data

            elif 500 <= response.status_code < 600:
                logger.error(
                    f"Server error (status {response.status_code}). Retrying in {2 ** attempt} seconds..."
                )
                time.sleep(2**attempt)
            else:
                raise ValueError(
                    f"Failed to download audio. Status Code: {response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            logger.exception(f"Attempt {attempt + 1} failed with error: {e}")
            time.sleep(2**attempt)

    raise ValueError("ERROR_DOWNLOADING_AUDIO: Max retries exceeded")
