import requests
from retrying import retry

from agentic_eval.core_evals.fi_utils.exceptions import CustomException
from tfc.utils.http_timeouts import DEFAULT_HTTP_TIMEOUT


class RequestHelper:
    """
    class to make requests to the FI api
    """
    @staticmethod
    @retry(wait_fixed=100, stop_max_attempt_number=2)
    def make_post_request(endpoint: str, payload: dict, headers: dict):
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get('error', 'Unknown Error')
                details_message = response_json.get(
                    'details', {}).get('message', 'No Details')
                raise CustomException(
                    response.status_code, f'{error_message}: {details_message}')
        except requests.exceptions.RequestException as e:
            raise e
        except Exception as e:
            raise e

    @staticmethod
    @retry(wait_fixed=100, stop_max_attempt_number=2)
    def make_patch_request(endpoint: str, payload: dict, headers: dict):
        try:
            response = requests.patch(
                endpoint,
                json=payload,
                headers=headers,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code != 200:
                response_json = response.json()
                error_message = response_json.get('error', 'Unknown Error')
                details_message = response_json.get(
                    'details', {}).get('message', 'No Details')
                raise CustomException(
                    response.status_code, f'{error_message}: {details_message}')
        except requests.exceptions.RequestException as e:
            raise e
        except Exception as e:
            raise e
