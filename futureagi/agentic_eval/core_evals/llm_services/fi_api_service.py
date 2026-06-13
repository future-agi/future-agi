import requests
import structlog
from retrying import retry

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_utils.constants import API_BASE_URL
from agentic_eval.core_evals.fi_utils.evals_result import EvalPerformanceReport
from agentic_eval.core_evals.fi_utils.exceptions import CustomException
from agentic_eval.core_evals.fi_utils.fi_interfaces import (
    FiEvalRequestCreateRequest,
    FiEvalResultCreateRequest,
    FiExperiment,
    FiFilters,
    FiInference,
)
from agentic_eval.core_evals.keys.fi_api_key import FiApiKey
from tfc.utils.http_timeouts import DEFAULT_HTTP_TIMEOUT

# SDK_VERSION = pkg_resources.get_distribution("Fi").version
SDK_VERSION = "1.1.1"


class FiApiService:
    @staticmethod
    def _headers():
        Fi_api_key = FiApiKey.get_key()
        return {
            "Fi-api-key": Fi_api_key,
        }

    @staticmethod
    def fetch_inferences(filters: FiFilters | None, limit: int) -> list[FiInference]:
        """
        Load data from Fi API.
        """
        try:
            endpoint = f"{API_BASE_URL}/api/v1/sdk/prompt_run/fetch-by-filter"
            filters_dict = filters.to_dict() if filters is not None else {}
            json = {
                "limit": limit,
                **filters_dict,
            }
            json = {k: v for k, v in json.items() if v is not None}
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json=json,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            inferences = response.json()["data"]["inferences"]
            return [FiInference(**x) for x in inferences]
        except Exception as e:
            logger.error("Exception fetching inferences", e)
            return []

    @staticmethod
    def log_usage(eval_name: str, run_type: str):
        """
        Logs a usage event to Posthog via Fi.
        """

        if not FiApiKey.is_set():
            return
        try:
            endpoint = f"{API_BASE_URL}/api/v1/sdk/log-usage"
            requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json={
                    "sdkVersion": SDK_VERSION,
                    "evalName": eval_name,
                    "run_type": run_type,
                },
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
        except Exception:
            # Silent failure is ok here.
            pass

    @staticmethod
    @retry(wait_fixed=500, stop_max_attempt_number=3)
    def log_eval_results(
        Fi_eval_result_create_many_request: list[FiEvalResultCreateRequest],
    ):
        """
        Logs eval results to Fi
        """
        try:
            # Construct eval update requests
            endpoint = f"{API_BASE_URL}/api/v1/eval_result"
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json=Fi_eval_result_create_many_request,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            return response.json()
        except Exception as e:
            logger.error(
                "An error occurred while posting eval results",
                str(e),
            )
            raise

    @staticmethod
    def create_dataset(dataset: dict):
        """
        Creates a dataset by calling the Fi API
        """
        try:
            endpoint = f"{API_BASE_URL}/api/v1/dataset_v2"
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json=dataset,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            return response.json()["data"]["dataset"]
        except Exception:
            raise

    @staticmethod
    def fetch_dataset_rows(dataset_id: str, number_of_rows: int | None = None):
        """
        Fetch the dataset rows by calling the Fi API

        """
        try:
            if number_of_rows is None:
                number_of_rows = 20
            endpoint = f"{API_BASE_URL}/api/v1/dataset_v2/fetch-by-id/{dataset_id}?offset=0&limit={number_of_rows}&include_dataset_rows=true"
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            return response.json()["data"]["dataset_rows"]
        except Exception:
            raise

    @staticmethod
    def add_dataset_rows(dataset_id: str, rows: list[dict]):
        """
        Adds rows to a dataset by calling the Fi API.

        Parameters:
        - dataset_id (str): The ID of the dataset to which rows are added.
        - rows (List[Dict]): A list of rows to add to the dataset, where each row is represented as a dictionary.

        Returns:
        The API response data for the dataset after adding the rows.

        Raises:
        - CustomException: If the API call fails or returns an error.
        """
        try:
            endpoint = f"{API_BASE_URL}/api/v1/dataset_v2/{dataset_id}/add-rows"
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json={"dataset_rows": rows},
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            return response.json()["data"]
        except Exception:
            raise

    @staticmethod
    def create_eval_request(
        Fi_eval_request_create_request: FiEvalRequestCreateRequest,
    ):
        """
        Create eval request
        """
        try:
            endpoint = f"{API_BASE_URL}/api/v1/eval_request"
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json=Fi_eval_request_create_request,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            return response.json()
        except Exception as e:
            logger.error(
                "An error occurred while creating eval request",
                str(e),
            )
            raise

    def log_eval_performance_report(
        self, eval_request_id: str, report: EvalPerformanceReport
    ):
        """
        Logs the performance metrics for the evaluator.
        """
        try:
            endpoint = f"{API_BASE_URL}/api/v1/eval_performance_report"
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json={
                    "eval_request_id": eval_request_id,
                    "true_positives": report["true_positives"],
                    "false_positives": report["false_positives"],
                    "true_negatives": report["true_negatives"],
                    "false_negatives": report["false_negatives"],
                    "accuracy": report["accuracy"],
                    "precision": report["precision"],
                    "recall": report["recall"],
                    "f1_score": report["f1_score"],
                    "runtime": report["runtime"],
                    "dataset_size": report["dataset_size"],
                },
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            return response.json()
        except Exception as e:
            logger.error(
                "An error occurred while posting eval performance report",
                str(e),
            )
            raise

    @staticmethod
    def log_experiment(
        eval_request_id: str,
        experiment: FiExperiment,
    ):
        """
        Logs the experiment metadata to Fi.
        """
        try:
            endpoint = f"{API_BASE_URL}/api/v1/experiment"
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json={
                    "eval_request_id": eval_request_id,
                    "experiment_name": experiment["experiment_name"],
                    "experiment_description": experiment["experiment_description"],
                    "language_model_provider": experiment["language_model_provider"],
                    "language_model_id": experiment["language_model_id"],
                    "prompt_template": experiment["prompt_template"],
                    "dataset_name": experiment["dataset_name"],
                },
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            return response.json()
        except Exception as e:
            logger.error(
                "An error occurred while posting experiment metadata",
                str(e),
            )
            raise

    @staticmethod
    def log_eval_results_with_config(eval_results_with_config: dict):
        try:
            endpoint = f"{API_BASE_URL}/api/v1/eval_run/log-eval-results-sdk"
            response = requests.post(
                endpoint,
                headers=FiApiService._headers(),
                json=eval_results_with_config,
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if response.status_code == 401:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = "please check your Fi api key and try again"
                raise CustomException(error_message, {"details": details_message})
            elif response.status_code != 200 and response.status_code != 201:
                response_json = response.json()
                error_message = response_json.get("error", "Unknown Error")
                details_message = response_json.get("details", {}).get(
                    "message", "No Details"
                )
                raise CustomException(error_message, {"details": details_message})
            return response.json()
        except Exception:
            raise
