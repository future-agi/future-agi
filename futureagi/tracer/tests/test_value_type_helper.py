from model_hub.models.choices import DataTypeChoices
from tracer.utils.helper import determine_value_type


def test_determine_value_type_recognizes_audio_data_uri():
    value = "data:audio/wav;base64,UklGRg=="

    assert determine_value_type(value) == DataTypeChoices.AUDIO.value


def test_determine_value_type_preserves_image_data_uri_classification():
    value = "data:image/webp;base64,UklGRg=="

    assert determine_value_type(value) == DataTypeChoices.IMAGE.value
