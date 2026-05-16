import binascii
import csv
import hashlib
import io
import json
import os
import re
import secrets
import string
import sys
import uuid

import structlog
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_402_PAYMENT_REQUIRED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from tfc.utils.contants import (
    ERROR_MESSAGE_KEY_NAME,
    ERROR_STATUS_CODE,
    RESULT_KEY_NAME,
    STATUS_KEY_NAME,
    SUCCESS_STATUS_CODE,
)

logger = structlog.get_logger(__name__)


class GeneralMethods:
    """
    General class for persistent methods which will be used across project.
    """

    request = None

    def __init__(self, request=None):
        self.request = request

    def update_request(self, request):
        """
        Updates request with given request.
        :param request:
        :return:
        """
        self.request = request

    def get_request_data(self, request=None):
        """
        Function:       get_request_data(self, request)
        Description:    To get the json data from request
        """
        # try:
        if request:
            data = request.data
        else:
            data = self.request.data
        # except:
        # data = json.loads(request.body.decode("utf-8"))
        return data

    def get_request_body(self, request=None):
        """
        Function:       get_request(self, request)
        Description:    To get all the data from request
        """
        if request:
            data = request
        else:
            data = self.request
        query_params = data.query_params
        body = data.data
        headers = self.get_headers(request)
        return query_params, body, headers

    def get_headers(self, request=None):
        """
        Function:       get_headers(self, request)
        Description:    To get all the headers from request
        """
        if request:
            data = request
        else:
            data = self.request
        regex = re.compile("^HTTP_")
        return {
            regex.sub("", header): value
            for (header, value) in data.META.items()
            if header.startswith("HTTP_")
        }

    def error_log(self, api_view: str, code: str, message: str):
        """
        Generates error log with a code location, code and message
        :param api_view:
        :param code:
        :param message:
        :return:
        """
        logger.error(f"ERROR: {code}: {api_view} - {message}")

    def warning_log(self, api_view: str, code: str, message: str):
        """
        Generates warning_log  with a code location,  code and message
        :param api_view:
        :param code:
        :param message:
        :return:
        """
        logger.warning(f"WARNING: {code}: {api_view} - {message}")

    def info_log(self, api_view: str, code: str, message: str):
        """
        Generates info_log  with a code location,  code and message
        :param api_view:
        :param code:
        :param message:
        :return:
        """
        logger.info(f"INFO: {code}: {api_view} - {message}")

    def debug_log(self, api_view: str, code: str, message: str):
        """
        Generates debug_log  with a code location,  code and message
        :param api_view:
        :param code:
        :param message:
        :return:
        """
        logger.debug(f"DEBUG: {code}: {api_view} - {message}")

    def critical_log(self, api_view: str, code: str, message: str):
        """
        Generates critical_log  with a code location,  code and message
        :param api_view:
        :param code:
        :param message:
        :return:
        """
        logger.critical(f"CRITICAL: {code}: {api_view} - {message}")

    def get_client_ip(self, request):
        """
        Fetches IP address of client.
        :param request:
        :return:
        """
        try:
            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
            if x_forwarded_for:
                ip = x_forwarded_for.split(",")[0]
            else:
                ip = request.META.get("REMOTE_ADDR")
            return ip
        except Exception as e:
            logger.error(e, exc_info=True)
            return False

    @staticmethod
    def generate_token():
        """
        Generates token for authentication.
        :return:
        """
        return binascii.hexlify(os.urandom(20)).decode()

    @staticmethod
    def uuid():
        """
        Generates random id.
        :return:
        """
        return uuid.uuid4()

    @staticmethod
    def is_valid_email(email):
        """
        Validates email-id.
        :param email:
        :return:
        """
        if (
            re.match(
                r"^[_a-z0-9-]+(\.[_a-z0-9-]+)*@[a-z0-9-]+(\.[a-z0-9-]+)*(\.[a-z]{2,4})$",
                email,
            )
            is None
        ):
            return False
        return True

    @staticmethod
    def is_string_and_number(data, with_space=True):
        """
        Validates if data has string and number with toggle for if space is included.
        :param data:
        :param with_space:
        :return:
        """
        # Includes space also
        regex = "^[ a-zA-Z0-9]+$"
        if not with_space:
            regex = "^[a-zA-Z0-9]+$"
        if re.match(regex, data) is None:
            return False
        return True

    def md5(self, data):
        """
        accepts string and Returns the encoded data in hexadecimal format
        Note: MD5 is used here for non-security purposes (checksums/identifiers only)
        """
        return hashlib.md5(data.encode("utf-8"), usedforsecurity=False).hexdigest()

    def is_valid_json(self, data):
        """
        Checks if json is valid or not
        :param data:
        :return:
        """
        try:
            json.loads(data)
            return True
        except json.JSONDecodeError:
            return False

    """
        Informational:            100 <= http status code <= 199
        Success:                  200 <= http status code <= 299
        Redirect:                 300 <= http status code <= 399
        Client Error:             400 <= http status code <= 499
        Server Error:             500 <= http status code <= 599
    """

    def _error_message(self, result):
        if isinstance(result, str) or result is None:
            return result
        return result

    def bad_request(self, result):
        """
        Gives bad_request response with status code=400 with given message.
        :param result:
        :return:
        """
        response = {}
        response[STATUS_KEY_NAME] = ERROR_STATUS_CODE
        response[RESULT_KEY_NAME] = result
        response[ERROR_MESSAGE_KEY_NAME] = self._error_message(result)
        return Response(response, status=HTTP_400_BAD_REQUEST)

    def usage_limit_response(self, check_result):
        """Return structured 402 response for usage/credit limit errors.

        Preserves error_code, upgrade_cta, and usage stats so the frontend
        can show contextual upgrade nudges instead of generic error messages.
        """
        response = {
            STATUS_KEY_NAME: ERROR_STATUS_CODE,
            RESULT_KEY_NAME: check_result.reason or "Usage limit exceeded",
            "error_code": check_result.error_code,
            "dimension": check_result.dimension,
            "current_usage": check_result.current_usage,
            "limit": check_result.limit,
        }
        if check_result.upgrade_cta:
            response["upgrade_cta"] = check_result.upgrade_cta.model_dump()
        return Response(response, status=HTTP_402_PAYMENT_REQUIRED)

    def not_found(self, result="Not found"):
        """
        Gives not_found response with status code=404 with given message.
        :param result:
        :return:
        """
        message = None
        if type(result) is not str:
            keys = list(dict(result).keys())
            if len(keys) > 0:
                try:
                    message = f"{keys[0]}: " + str(dict(result).get(keys[0])[0])
                except (KeyError, IndexError, AttributeError, TypeError) as e:
                    logger.error(e, exc_info=True)
                    result = result
        elif type(result) is str:
            message = result
        response = {}
        response[STATUS_KEY_NAME] = ERROR_STATUS_CODE
        response[RESULT_KEY_NAME] = result
        response[ERROR_MESSAGE_KEY_NAME] = message
        return Response(response, status=HTTP_404_NOT_FOUND)

    def success_response(self, result, status=HTTP_200_OK):
        """
        Gives success_response with the given message and status code.
        :param result: The result to be returned in the response
        :param status: The HTTP status code (default is 200 OK)
        :return: Response object
        """
        response = {}
        response[STATUS_KEY_NAME] = SUCCESS_STATUS_CODE
        response[RESULT_KEY_NAME] = result
        return Response(response, status=status)

    def create_response(
        self,
        result,
    ):
        """
        Gives created response  with status code=201 with given message.
        :param result:
        :return:
        """
        response = {}
        response[STATUS_KEY_NAME] = SUCCESS_STATUS_CODE
        response[RESULT_KEY_NAME] = result
        # response[ERROR_MESSAGE_KEY_NAME] = SUCCESS_MESSAGE
        # if programatic_response:
        #     response = dict()
        #     response[RESULT_KEY_NAME] = result
        return Response(response, status=HTTP_201_CREATED)

    def internal_server_error_response(self, result="Internal Server Error"):
        """
        Gives created internal_server_error_response with status code=500 with given message.
        :param result:
        :return:
        """
        response = {}
        response[STATUS_KEY_NAME] = ERROR_STATUS_CODE
        response[ERROR_MESSAGE_KEY_NAME] = self._error_message(result)
        # if programatic_response:
        #     response = dict()
        response[RESULT_KEY_NAME] = result
        return Response(response, status=HTTP_500_INTERNAL_SERVER_ERROR)

    def param_missing_response(self, key, message):
        """
        Gives created param_missing_response with status code=400 with given message.
        :param result:
        :return:
        """
        response = {}
        result = {}
        errors = []
        errors.append(message)
        result.update({key: errors})
        response[STATUS_KEY_NAME] = ERROR_STATUS_CODE
        response[RESULT_KEY_NAME] = result
        response[ERROR_MESSAGE_KEY_NAME] = result
        return Response(response, status=HTTP_400_BAD_REQUEST)

    def unauthorized_response(self):
        """
        Gives created unauthorized_response with status code=401 with given message.
        :param result:
        :return:
        """
        response = {}
        response[STATUS_KEY_NAME] = ERROR_STATUS_CODE
        response[RESULT_KEY_NAME] = (
            "The request requires authentication. Please login to continue."
        )
        response[ERROR_MESSAGE_KEY_NAME] = (
            "The request requires authentication. Please login to continue."
        )
        return Response(response, status=HTTP_401_UNAUTHORIZED)

    def forbidden_response(self, message="You don't have access to this api"):
        response = {}
        response[STATUS_KEY_NAME] = ERROR_STATUS_CODE
        response[RESULT_KEY_NAME] = message
        response[ERROR_MESSAGE_KEY_NAME] = message
        return Response(response, status=HTTP_403_FORBIDDEN)

    def custom_error_response(self, status_code, result):
        """
        Gives created custom_error_response with custom status code with given message.
        :param result:
        :return:
        """
        response = {}
        response[STATUS_KEY_NAME] = ERROR_STATUS_CODE
        response[RESULT_KEY_NAME] = result
        response[ERROR_MESSAGE_KEY_NAME] = self._error_message(result)
        return Response(response, status=status_code)

    def detect_delimiter(self, file):
        sample = file.read(1024).decode("utf-8")
        file.seek(0)  # Reset file pointer to the beginning
        delimiters = [",", ";", "\t", "|", " "]
        best_delimiter = delimiters[0]
        max_columns = 0

        for delimiter in delimiters:
            reader = csv.reader(io.StringIO(sample), delimiter=delimiter)
            num_columns = len(next(reader))
            if num_columns > max_columns:
                max_columns = num_columns
                best_delimiter = delimiter

        return best_delimiter

    def detect_delimiter_stringio(self, csv_string_io):
        """
        Detects the delimiter of a CSV string.
        :param csv_string_io: StringIO object containing the CSV data.
        :return: Detected delimiter.
        """
        # Increase the field size limit
        csv.field_size_limit(sys.maxsize)
        sample = csv_string_io.read(1024)  # Read a sample from the StringIO object
        csv_string_io.seek(0)  # Reset StringIO object for further reading
        sniffer = csv.Sniffer()
        try:
            dialect = sniffer.sniff(sample)
            return dialect.delimiter
        except csv.Error:
            # Common delimiters to try
            common_delimiters = [",", ";", "\t", "|", ":"]
            max_columns = 0
            best_delimiter = ","

            for delimiter in common_delimiters:
                csv_string_io.seek(0)
                reader = csv.reader(csv_string_io, delimiter=delimiter)
                line_lengths = [len(row) for row in reader]

                # Determine the consistency of column numbers and the maximum columns found
                unique_lengths = set(line_lengths)
                if len(unique_lengths) == 1:  # All rows have the same number of columns
                    if line_lengths[0] > max_columns:
                        max_columns = line_lengths[0]
                        best_delimiter = delimiter
                elif len(unique_lengths) == 2 and min(unique_lengths) == 0:
                    # Handle cases where there's a mix of empty lines
                    line_lengths = [length for length in line_lengths if length > 0]
                    if len(set(line_lengths)) == 1 and line_lengths[0] > max_columns:
                        max_columns = line_lengths[0]
                        best_delimiter = delimiter

            return best_delimiter

    def generate_random_text(self, length=10):
        letters = string.ascii_letters
        # Use secrets module for cryptographically strong random choices
        return "".join(secrets.choice(letters) for _ in range(length))

    def too_many_requests(self, message="Too many requests. Please try again later."):
        """
        Gives created too_many_requests response with status code=429 with given message.
        :param result:
        :return:
        """
        response = {}
        response[STATUS_KEY_NAME] = ERROR_STATUS_CODE
        response[RESULT_KEY_NAME] = message
        return Response(response, status=HTTP_429_TOO_MANY_REQUESTS)
