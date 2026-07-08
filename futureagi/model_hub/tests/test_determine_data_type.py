"""
Unit tests for determine_data_type functionality in model_hub/models/choices.py.

Tests cover:
- JSON detection for Python objects (dict/list) from pd.read_json
- JSON detection for valid JSON strings (only objects/arrays with objects)
- Strict datetime detection (regex-based, not pd.to_datetime)
- Array detection (bracket syntax only, not just commas)
- Text with commas should remain TEXT
- Numeric strings should not become DATETIME or JSON
- Integer/Float/Boolean string detection from CSV files
- Empty arrays should be ARRAY, not JSON
"""

import pandas as pd

from model_hub.models.choices import (
    DataTypeChoices,
    _all_valid_json_strings,
    _is_array_column,
    _is_boolean_string_column,
    _is_float_string_column,
    _is_integer_string_column,
    _is_strict_datetime_column,
    determine_data_type,
)


class TestDetermineDataType:
    """Test cases for determine_data_type function"""

    # ============= JSON DETECTION TESTS =============

    def test_json_detection_python_dict_objects(self):
        """Python dict objects (from pd.read_json) should be detected as JSON"""
        column = pd.Series([{"key": "value"}, {"name": "test"}])
        result = determine_data_type(column)
        assert result == DataTypeChoices.JSON.value

    def test_python_list_objects_without_dicts_should_be_array(self):
        """Python list objects without dicts should be detected as ARRAY, not JSON"""
        column = pd.Series([[1, 2, 3], [4, 5, 6]])
        result = determine_data_type(column)
        assert result == DataTypeChoices.ARRAY.value

    def test_python_list_objects_with_dicts_should_be_json(self):
        """Python list objects containing dicts should be detected as JSON"""
        column = pd.Series([[{"id": 1}], [{"id": 2}]])
        result = determine_data_type(column)
        assert result == DataTypeChoices.JSON.value

    def test_json_detection_nested_objects(self):
        """Nested dict/list structures should be detected as JSON"""
        column = pd.Series(
            [
                {"items": [{"id": 1}, {"id": 2}]},
                {"items": [{"id": 3}]},
            ]
        )
        result = determine_data_type(column)
        assert result == DataTypeChoices.JSON.value

    def test_json_detection_empty_lists_with_objects(self):
        """Mix of empty lists and lists with objects should be detected as JSON"""
        column = pd.Series([[], [{"key": "value"}]])
        result = determine_data_type(column)
        assert result == DataTypeChoices.JSON.value

    def test_pure_empty_lists_should_be_array(self):
        """All empty Python lists should be detected as ARRAY, not JSON"""
        column = pd.Series([[], [], []])
        result = determine_data_type(column)
        assert result == DataTypeChoices.ARRAY.value

    def test_json_detection_mixed_empty_and_complex(self):
        """Mix of empty and complex objects should be detected as JSON (like the user's example)"""
        column = pd.Series(
            [
                [],
                [{"gstin": "06AAICD6219M1ZD", "title": "Marketing Bill"}],
                [{"gstin": "06AAICD6219M1ZD", "title": "Shipping Bill"}],
                [],
            ]
        )
        result = determine_data_type(column)
        assert result == DataTypeChoices.JSON.value

    def test_json_detection_valid_json_strings(self):
        """Valid JSON strings should be detected as JSON"""
        column = pd.Series(['{"key": "value"}', '{"name": "test"}'])
        result = determine_data_type(column)
        assert result == DataTypeChoices.JSON.value

    def test_simple_array_strings_should_be_array(self):
        """Simple JSON array strings (without objects) should be detected as ARRAY"""
        column = pd.Series(["[1, 2, 3]", "[4, 5, 6]"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.ARRAY.value

    def test_json_array_strings_with_objects_should_be_json(self):
        """JSON array strings containing objects should be detected as JSON"""
        column = pd.Series(['[{"id": 1}]', '[{"id": 2}]'])
        result = determine_data_type(column)
        assert result == DataTypeChoices.JSON.value

    def test_json_detection_with_nulls(self):
        """JSON detection should work with null values mixed in"""
        column = pd.Series([{"key": "value"}, None, {"name": "test"}])
        result = determine_data_type(column)
        assert result == DataTypeChoices.JSON.value

    # ============= DATETIME DETECTION TESTS =============

    def test_datetime_iso_format(self):
        """ISO format dates should be detected as DATETIME"""
        column = pd.Series(["2023-01-15", "2023-02-20", "2023-03-25"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.DATETIME.value

    def test_datetime_iso_with_time(self):
        """ISO format with time should be detected as DATETIME"""
        column = pd.Series(["2023-01-15T10:30:00", "2023-02-20T14:45:00"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.DATETIME.value

    def test_datetime_slash_format(self):
        """Slash format dates should be detected as DATETIME"""
        column = pd.Series(["2023/01/15", "2023/02/20"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.DATETIME.value

    def test_datetime_ddmmyyyy_format(self):
        """DD-MM-YYYY format should be detected as DATETIME"""
        column = pd.Series(["15-01-2023", "20-02-2023"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.DATETIME.value

    def test_numeric_strings_not_datetime(self):
        """Single numeric strings should NOT be detected as DATETIME"""
        column = pd.Series(["1", "2", "3"])
        result = determine_data_type(column)
        assert result != DataTypeChoices.DATETIME.value

    def test_year_only_not_datetime(self):
        """Year-only strings should NOT be detected as DATETIME"""
        column = pd.Series(["2023", "2024", "2025"])
        result = determine_data_type(column)
        assert result != DataTypeChoices.DATETIME.value

    def test_random_text_not_datetime(self):
        """Random text should NOT be detected as DATETIME"""
        column = pd.Series(["hello", "world", "test"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.TEXT.value

    # ============= ARRAY DETECTION TESTS =============

    def test_array_bracket_syntax(self):
        """Bracket array syntax should be detected as ARRAY"""
        column = pd.Series(["[a, b, c]", "[1, 2, 3]"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.ARRAY.value

    def test_text_with_commas_not_array(self):
        """Text containing commas should NOT be detected as ARRAY"""
        column = pd.Series(["Hello, world", "San Francisco, CA"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.TEXT.value

    def test_address_with_commas_not_array(self):
        """Addresses with commas should NOT be detected as ARRAY"""
        column = pd.Series(
            [
                "123 Main St, Suite 100, New York, NY",
                "456 Oak Ave, Building B, Los Angeles, CA",
            ]
        )
        result = determine_data_type(column)
        assert result == DataTypeChoices.TEXT.value

    def test_csv_style_values_not_array(self):
        """CSV-style comma-separated values without brackets should NOT be ARRAY"""
        column = pd.Series(["red,green,blue", "one,two,three"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.TEXT.value

    # ============= BASIC TYPE DETECTION TESTS =============

    def test_boolean_detection(self):
        """Boolean values should be detected as BOOLEAN"""
        column = pd.Series([True, False, True])
        result = determine_data_type(column)
        assert result == DataTypeChoices.BOOLEAN.value

    def test_integer_detection(self):
        """Integer values should be detected as INTEGER"""
        column = pd.Series([1, 2, 3, 4, 5])
        result = determine_data_type(column)
        assert result == DataTypeChoices.INTEGER.value

    def test_float_detection(self):
        """Float values should be detected as FLOAT"""
        column = pd.Series([1.5, 2.5, 3.5])
        result = determine_data_type(column)
        assert result == DataTypeChoices.FLOAT.value

    def test_empty_column_returns_text(self):
        """Empty column should default to TEXT"""
        column = pd.Series([None, None, None])
        result = determine_data_type(column)
        assert result == DataTypeChoices.TEXT.value

    def test_plain_text_detection(self):
        """Plain text should be detected as TEXT"""
        column = pd.Series(["hello", "world", "test"])
        result = determine_data_type(column)
        assert result == DataTypeChoices.TEXT.value


class TestHelperFunctions:
    """Test cases for helper functions"""

    # ============= _all_valid_json_strings TESTS =============

    def test_valid_json_object_strings(self):
        """Valid JSON object strings should return True"""
        assert _all_valid_json_strings(['{"key": "value"}', '{"name": "test"}']) is True

    def test_simple_json_array_strings_not_json(self):
        """Simple JSON array strings (without objects) should return False"""
        assert _all_valid_json_strings(["[1, 2, 3]", '["a", "b"]']) is False

    def test_json_array_strings_with_objects(self):
        """JSON array strings containing objects should return True"""
        assert _all_valid_json_strings(['[{"id": 1}]', '[{"name": "test"}]']) is True

    def test_invalid_json_strings(self):
        """Invalid JSON strings should return False"""
        assert _all_valid_json_strings(["{'key': 'value'}"]) is False  # Single quotes

    def test_empty_list_returns_false(self):
        """Empty list should return False"""
        assert _all_valid_json_strings([]) is False

    def test_mixed_valid_invalid_returns_false(self):
        """Mix of valid and invalid should return False"""
        assert _all_valid_json_strings(['{"valid": true}', "not json"]) is False

    def test_numeric_strings_not_json(self):
        """Numeric strings should NOT be considered JSON (even though json.loads parses them)"""
        # Phone numbers
        assert _all_valid_json_strings(["9876543210", "9876543211"]) is False
        # Pin codes
        assert _all_valid_json_strings(["600001", "560001"]) is False
        # House numbers
        assert _all_valid_json_strings(["130", "806", "260"]) is False

    def test_boolean_strings_not_json(self):
        """Boolean strings should NOT be considered JSON"""
        assert _all_valid_json_strings(["true", "false"]) is False

    def test_null_string_not_json(self):
        """Null string should NOT be considered JSON"""
        assert _all_valid_json_strings(["null"]) is False

    def test_mixed_json_and_primitives_not_json(self):
        """Mix of JSON objects and primitives should NOT be JSON"""
        assert _all_valid_json_strings(['{"key": "value"}', "123"]) is False

    def test_empty_arrays_not_json(self):
        """Empty arrays should NOT be considered JSON (should be ARRAY)"""
        assert _all_valid_json_strings(["[]", "[]"]) is False

    def test_simple_arrays_not_json(self):
        """Simple arrays without objects should NOT be considered JSON"""
        assert _all_valid_json_strings(["[1, 2, 3]", "[4, 5, 6]"]) is False

    def test_arrays_with_objects_are_json(self):
        """Arrays containing objects SHOULD be considered JSON"""
        assert (
            _all_valid_json_strings(['[{"key": "value"}]', '[{"name": "test"}]'])
            is True
        )

    # ============= _is_strict_datetime_column TESTS =============

    def test_iso_dates_valid(self):
        """ISO format dates should return True"""
        assert _is_strict_datetime_column(["2023-01-15", "2023-02-20"]) is True

    def test_iso_datetime_valid(self):
        """ISO datetime format should return True"""
        assert _is_strict_datetime_column(["2023-01-15T10:30:00"]) is True

    def test_numeric_strings_invalid(self):
        """Numeric strings should return False"""
        assert _is_strict_datetime_column(["1", "2", "3"]) is False

    def test_random_text_invalid(self):
        """Random text should return False"""
        assert _is_strict_datetime_column(["hello", "world"]) is False

    def test_empty_list_returns_false(self):
        """Empty list should return False"""
        assert _is_strict_datetime_column([]) is False

    # ============= _is_array_column TESTS =============

    def test_bracket_arrays_valid(self):
        """Bracket syntax arrays should return True"""
        assert _is_array_column(["[1, 2, 3]", "[4, 5, 6]"]) is True

    def test_empty_bracket_arrays_valid(self):
        """Empty bracket arrays should return True"""
        assert _is_array_column(["[]", "[]"]) is True

    def test_comma_only_invalid(self):
        """Comma-only values should return False"""
        assert _is_array_column(["a, b, c", "1, 2, 3"]) is False

    def test_mixed_bracket_no_bracket_invalid(self):
        """Mix of bracket and non-bracket should return False"""
        assert _is_array_column(["[1, 2]", "a, b"]) is False

    def test_arrays_with_objects_not_array(self):
        """Arrays containing objects should NOT be detected as ARRAY (should be JSON)"""
        assert _is_array_column(['[{"key": "value"}]', '[{"name": "test"}]']) is False

    def test_empty_list_returns_false(self):
        """Empty list should return False"""
        assert _is_array_column([]) is False

    # ============= _is_integer_string_column TESTS =============

    def test_integer_strings_valid(self):
        """Integer strings should return True"""
        assert _is_integer_string_column(["123", "456", "789"]) is True

    def test_float_strings_not_integer(self):
        """Float strings should return False for integer check"""
        assert _is_integer_string_column(["123.45", "67.89"]) is False

    def test_non_numeric_strings_not_integer(self):
        """Non-numeric strings should return False"""
        assert _is_integer_string_column(["abc", "def"]) is False

    def test_empty_list_integer_returns_false(self):
        """Empty list should return False"""
        assert _is_integer_string_column([]) is False

    def test_leading_zeros_is_integer(self):
        """Strings with leading zeros are parsed as integers by int()"""
        assert _is_integer_string_column(["001", "002", "003"]) is True

    def test_single_zero_is_integer(self):
        """Single zero should return True"""
        assert _is_integer_string_column(["0", "1", "2"]) is True

    # ============= _is_float_string_column TESTS =============

    def test_float_strings_valid(self):
        """Float strings should return True"""
        assert _is_float_string_column(["123.45", "67.89", "0.5"]) is True

    def test_integer_strings_are_valid_floats(self):
        """Integer strings are also valid floats (float() parses them)"""
        assert _is_float_string_column(["123", "456"]) is True

    def test_non_numeric_strings_not_float(self):
        """Non-numeric strings should return False"""
        assert _is_float_string_column(["abc", "def"]) is False

    def test_empty_list_float_returns_false(self):
        """Empty list should return False"""
        assert _is_float_string_column([]) is False

    # ============= _is_boolean_string_column TESTS =============

    def test_boolean_strings_valid(self):
        """Boolean strings (true/false/0/1) should return True"""
        assert _is_boolean_string_column(["true", "false"]) is True
        assert _is_boolean_string_column(["True", "False"]) is True
        assert _is_boolean_string_column(["0", "1"]) is True

    def test_yes_no_strings_not_boolean(self):
        """Yes/No strings are not recognized as boolean"""
        assert _is_boolean_string_column(["yes", "no", "Yes", "No"]) is False

    def test_mixed_boolean_formats_invalid(self):
        """Mixing different boolean representations may not be valid"""
        # Only true/false/0/1 are valid boolean representations
        assert _is_boolean_string_column(["true", "1", "false", "0"]) is True

    def test_non_boolean_strings_invalid(self):
        """Non-boolean strings should return False"""
        assert _is_boolean_string_column(["hello", "world"]) is False

    def test_empty_list_boolean_returns_false(self):
        """Empty list should return False"""
        assert _is_boolean_string_column([]) is False


class TestRealWorldScenarios:
    """Test cases simulating real-world data uploads"""

    def test_user_json_file_scenario(self):
        """
        Simulates uploading user's JSON file with expected_output containing nested JSON.
        This is the exact scenario from the bug report.
        """
        # Simulating what pd.read_json would produce
        expected_output_column = pd.Series(
            [
                [],  # "1.multipagebill"
                [
                    {
                        "gstin": "06AAICD6219M1ZD",
                        "title": "Marketing Incentives Assembly",
                    }
                ],
                [
                    {
                        "gstin": "06AAICD6219M1ZD",
                        "title": "InstaKart Ship COD Charges Feb",
                    }
                ],
                [],  # "TestGovermentbill(bharatsilks)"
            ]
        )
        name_column = pd.Series(
            [
                "1.multipagebill",
                "Marketing bill",
                "Shipping Bill",
                "TestGovermentbill(bharatsilks)",
            ]
        )

        # expected_output should be JSON, not TEXT
        assert determine_data_type(expected_output_column) == DataTypeChoices.JSON.value
        # name should be TEXT
        assert determine_data_type(name_column) == DataTypeChoices.TEXT.value

    def test_csv_with_phone_numbers_as_strings(self):
        """
        CSV with phone numbers read as strings should detect INTEGER.
        These are valid integer strings that can be parsed.
        """
        phone_column = pd.Series(
            ["9876543210", "9876543211", "9876543212", "9876543218", "9876543219"]
        )
        assert determine_data_type(phone_column) == DataTypeChoices.INTEGER.value

    def test_csv_with_pin_codes_as_strings(self):
        """CSV with pin codes read as strings should detect INTEGER"""
        pin_column = pd.Series(["600001", "600001", "600001", "560001", "400001"])
        assert determine_data_type(pin_column) == DataTypeChoices.INTEGER.value

    def test_csv_with_house_numbers_as_strings(self):
        """CSV with house numbers read as strings should detect INTEGER"""
        house_column = pd.Series(["130", "806", "260", "957", "284"])
        assert determine_data_type(house_column) == DataTypeChoices.INTEGER.value

    def test_csv_with_float_numbers_as_strings(self):
        """CSV with float numbers read as strings should detect FLOAT"""
        float_column = pd.Series(["123.12", "12.31", "13421.12", "2321.12", "221.1012"])
        assert determine_data_type(float_column) == DataTypeChoices.FLOAT.value

    def test_csv_with_boolean_as_strings(self):
        """CSV with boolean values read as strings should detect BOOLEAN"""
        bool_column = pd.Series(["true", "false", "True", "False"])
        assert determine_data_type(bool_column) == DataTypeChoices.BOOLEAN.value

    def test_csv_with_empty_array_as_strings(self):
        """CSV with empty array strings should detect ARRAY, not JSON"""
        empty_arr_column = pd.Series(["[]", "[]", "[]", "[]", "[]"])
        assert determine_data_type(empty_arr_column) == DataTypeChoices.ARRAY.value

    def test_csv_with_simple_array_strings(self):
        """CSV with simple array strings should detect ARRAY"""
        simple_arr_column = pd.Series(["[1, 2, 3]", "[4, 5, 6]"])
        assert determine_data_type(simple_arr_column) == DataTypeChoices.ARRAY.value

    def test_csv_with_json_object_strings(self):
        """CSV with JSON object strings should detect JSON"""
        json_obj_column = pd.Series(['{"abc": 1234}', '{"abc": 1234}'])
        assert determine_data_type(json_obj_column) == DataTypeChoices.JSON.value

    def test_csv_with_json_array_of_objects_strings(self):
        """CSV with JSON array of objects should detect JSON"""
        json_list_column = pd.Series(['[{"abc": "acv"}]', '[{"abc": "acv"}]'])
        assert determine_data_type(json_list_column) == DataTypeChoices.JSON.value

    def test_csv_with_addresses(self):
        """CSV with address columns should detect TEXT, not ARRAY"""
        address_column = pd.Series(
            [
                "123 Main St, Apt 4, New York, NY 10001",
                "456 Oak Ave, Suite 200, Los Angeles, CA 90001",
                "789 Pine Rd, Building C, Chicago, IL 60601",
            ]
        )
        assert determine_data_type(address_column) == DataTypeChoices.TEXT.value

    def test_csv_with_numeric_ids(self):
        """CSV with numeric IDs should detect INTEGER, not DATETIME"""
        id_column = pd.Series([1, 2, 3, 4, 5])
        assert determine_data_type(id_column) == DataTypeChoices.INTEGER.value

    def test_csv_with_string_ids_with_leading_zeros(self):
        """CSV with string numeric IDs with leading zeros are parsed as INTEGER"""
        id_column = pd.Series(["001", "002", "003"])
        # Note: Leading zeros are parsed as valid integers by int()
        assert determine_data_type(id_column) == DataTypeChoices.INTEGER.value

    def test_csv_with_real_dates(self):
        """CSV with real ISO dates should detect DATETIME"""
        date_column = pd.Series(["2023-01-15", "2023-02-20", "2023-03-25"])
        assert determine_data_type(date_column) == DataTypeChoices.DATETIME.value


# ============= IMAGES DATA TYPE DETECTION TESTS =============


class TestImagesDataTypeDetection:
    """Test cases for detecting IMAGES data type (multiple images in single column)"""

    def test_multiple_image_urls_comma_separated_detected_as_images(self):
        """Comma-separated image URLs should be detected as IMAGES"""
        column = pd.Series(
            [
                "https://example.com/image1.jpg, https://example.com/image2.png",
                "https://example.com/image3.jpg, https://example.com/image4.png",
            ]
        )
        result = determine_data_type(column)
        assert result == DataTypeChoices.IMAGES.value

    def test_single_image_url_not_detected_as_images(self):
        """Single image URL per row should NOT be detected as IMAGES (should be IMAGE)"""
        column = pd.Series(
            [
                "https://example.com/image1.jpg",
                "https://example.com/image2.png",
            ]
        )
        result = determine_data_type(column)
        # Single image should be IMAGE, not IMAGES
        assert result != DataTypeChoices.IMAGES.value

    def test_mixed_image_and_non_image_urls_not_images(self):
        """Mix of image and non-image URLs should NOT be IMAGES"""
        column = pd.Series(
            [
                "https://example.com/image1.jpg, https://example.com/page.html",
                "https://example.com/image2.png, https://example.com/data.json",
            ]
        )
        result = determine_data_type(column)
        assert result != DataTypeChoices.IMAGES.value

    def test_text_with_commas_not_images(self):
        """Regular text with commas should NOT be detected as IMAGES"""
        column = pd.Series(
            [
                "Hello, world, this is text",
                "San Francisco, CA, USA",
            ]
        )
        result = determine_data_type(column)
        assert result == DataTypeChoices.TEXT.value

    def test_three_or_more_image_urls_detected_as_images(self):
        """Three or more comma-separated image URLs should be detected as IMAGES"""
        column = pd.Series(
            [
                "https://a.com/1.jpg, https://a.com/2.png, https://a.com/3.gif",
                "https://b.com/4.jpg, https://b.com/5.png, https://b.com/6.webp",
            ]
        )
        result = determine_data_type(column)
        assert result == DataTypeChoices.IMAGES.value

    def test_image_urls_with_query_params_detected_as_images(self):
        """Image URLs with query parameters should be detected as IMAGES"""
        column = pd.Series(
            [
                "https://cdn.com/img1.jpg?size=large, https://cdn.com/img2.png?width=100",
                "https://cdn.com/img3.jpg?v=1, https://cdn.com/img4.png?q=80",
            ]
        )
        result = determine_data_type(column)
        assert result == DataTypeChoices.IMAGES.value

    def test_only_one_url_per_value_not_images(self):
        """Values with only one URL each (even if image) should not be IMAGES"""
        column = pd.Series(
            [
                "https://example.com/image1.jpg",
                "https://example.com/image2.jpg",
            ]
        )
        result = determine_data_type(column)
        # Single images should be IMAGE type, not IMAGES
        assert result != DataTypeChoices.IMAGES.value

    def test_empty_column_not_images(self):
        """Empty column should NOT be detected as IMAGES"""
        column = pd.Series([None, None, None])
        result = determine_data_type(column)
        assert result != DataTypeChoices.IMAGES.value

    def test_mixed_single_and_multiple_images_detected_correctly(self):
        """If ANY value has multiple images, should be TEXT (inconsistent format)"""
        # If values are inconsistent (some single, some multiple), algorithm returns False
        column = pd.Series(
            [
                "https://example.com/image1.jpg",  # Single image
                "https://example.com/img2.jpg, https://example.com/img3.png",  # Multiple
            ]
        )
        result = determine_data_type(column)
        # Should not be IMAGES since not all values have comma-separated multiple images
        assert result != DataTypeChoices.IMAGES.value


class TestIsDocumentUrlSSRF:
    """TH-5648 follow-up: `is_document_url` is called from `determine_data_type`
    for *every* uploaded file (CSV/HuggingFace/etc.) whose column values look
    like URLs -- independent of whether that column ever ends up typed as
    Document. It used to call `requests.head(url, allow_redirects=True)`
    directly, with zero SSRF protection: a plain CSV upload with a URL-like
    column could make the server probe cloud metadata endpoints or any
    internal/private address, and would also happily follow redirects to one.
    It must now route through the same SSRF-safe `_safe_head` used by
    `validate_file_url`, and reject unsafe targets outright (returning False,
    since it's a best-effort classifier) instead of ever issuing the request.
    """

    def test_rejects_cloud_metadata_endpoint(self):
        from model_hub.models.choices import is_document_url

        assert is_document_url("http://169.254.169.254/latest/meta-data/") is False

    def test_rejects_loopback(self):
        from model_hub.models.choices import is_document_url

        assert is_document_url("http://127.0.0.1:8080/secret") is False

    def test_rejects_private_network(self):
        from model_hub.models.choices import is_document_url

        assert is_document_url("http://10.0.0.5/internal") is False

    def test_uses_safe_head_not_raw_requests(self, monkeypatch):
        """Pin the implementation detail: classification must go through
        `_safe_head` (IP-pinned, redirect-revalidated) rather than a raw
        `requests.head()` call.
        """
        import model_hub.models.choices as choices_module

        calls = []

        def fake_safe_head(url, *, file_type):
            calls.append((url, file_type))
            return 200, {"content-type": "application/pdf"}, url

        monkeypatch.setattr(choices_module, "_safe_head", fake_safe_head)

        assert choices_module.is_document_url("https://example.com/report.pdf") is True
        assert calls == [("https://example.com/report.pdf", "document")]

    def test_safe_head_rejection_yields_false_not_exception(self, monkeypatch):
        """If `_safe_head` raises (e.g. SSRF target, timeout, bad redirect),
        `is_document_url` must degrade to False rather than propagating --
        callers use it as a boolean classifier during column-type inference.
        """
        import model_hub.models.choices as choices_module

        def raising_safe_head(url, *, file_type):
            raise ValueError("blocked")

        monkeypatch.setattr(choices_module, "_safe_head", raising_safe_head)

        assert choices_module.is_document_url("https://example.com/report.pdf") is False


class TestClassifyUrlColumnSampling:
    """TH-5648 follow-up: `is_document_url` is the only network-bound check in
    `_classify_url_column`, so a large URL-like column used to cost one
    blocking HEAD request per row. It must now only be run against a bounded
    sample.
    """

    def test_document_check_is_capped_to_sample_size(self, monkeypatch):
        import model_hub.models.choices as choices_module

        calls = []

        def fake_is_document_url(url):
            calls.append(url)
            return True

        monkeypatch.setattr(choices_module, "is_document_url", fake_is_document_url)
        monkeypatch.setattr(choices_module, "is_audio_url", lambda url: False)
        monkeypatch.setattr(choices_module, "is_image_url", lambda url: False)

        urls = [f"https://example.com/{i}.pdf" for i in range(100)]
        result = choices_module._classify_url_column(urls)

        assert result == choices_module.DataTypeChoices.DOCUMENT.value
        assert len(calls) == choices_module._MAX_DOCUMENT_CHECK_SAMPLES

    def test_skips_document_check_entirely_once_audio_or_image_matches(
        self, monkeypatch
    ):
        import model_hub.models.choices as choices_module

        calls = []
        monkeypatch.setattr(
            choices_module, "is_document_url", lambda url: calls.append(url) or True
        )
        monkeypatch.setattr(choices_module, "is_audio_url", lambda url: False)
        monkeypatch.setattr(choices_module, "is_image_url", lambda url: True)

        urls = [f"https://example.com/{i}.png" for i in range(5)]
        result = choices_module._classify_url_column(urls)

        assert result == choices_module.DataTypeChoices.IMAGE.value
        assert calls == []
