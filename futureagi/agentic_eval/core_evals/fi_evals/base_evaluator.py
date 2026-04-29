import traceback
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed

from tfc.telemetry import wrap_for_thread

from agentic_eval.core_evals.fi_utils.dataset_helper import (
    generate_eval_display_name,
    generate_unique_dataset_name,
)
from agentic_eval.core_evals.fi_utils.evals_result import (
    BatchRunResult,
    DataPoint,
    EvalResult,
    GuardResult,
)
from agentic_eval.core_evals.fi_utils.fi_dataset import Dataset
from agentic_eval.core_evals.fi_utils.fi_logging_helper import FiLoggingHelper
import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.llm_services.fi_api_service import FiApiService


class BaseEvaluator(ABC):

    # Abstract properties
    @property
    @abstractmethod
    def name(self) -> str:
        """A unique name identifier for the evaluator."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """A display name for the evaluator."""
        pass

    @property
    @abstractmethod
    def metric_ids(self) -> list[str]:
        """The metric computed by the evaluator."""
        pass

    @property
    @abstractmethod
    def required_args(self) -> list[str]:
        """A list of required arguments for the evaluator."""
        pass

    @property
    @abstractmethod
    def examples(self):
        """A list of examples for the evaluator."""
        pass

    @abstractmethod
    def is_failure(self, *args) -> bool | None:
        """A method to determine if the evaluation failed."""
        pass

    @abstractmethod
    def _evaluate(self, **kwargs) -> EvalResult:
        """The method that performs the evaluation."""
        pass

    def to_config(self) -> dict | None:
        return None

    # Common methods
    def _examples_str(self) -> str:
        return "" if self.examples is None else "\n".join(map(str, self.examples))


    def validate_args(self, **kwargs) -> None:
        """
        Validates that all required arguments are present and not None.
        """
        for arg in self.required_args:
            if arg not in kwargs:
                raise ValueError(f"Missing required argument: {arg}")
            elif kwargs[arg] is None:
                raise ValueError(f"{arg} cannot be None")

    def _validate_batch_args(self, data: list[DataPoint]) -> bool:
        """
        Validates that each entry in the batch has all the required arguments,
        and none of the arguments is None.
        """
        for i, entry in enumerate(data):
            for arg in self.required_args:
                if arg not in entry:
                    raise ValueError(
                        f"Data at index {i} is missing required argument: {arg}"
                    )
                entry_dict = dict(entry)  # Convert TypedDict to regular dict for dynamic access
                if entry_dict.get(arg) is None:
                    raise ValueError(
                        f"Data at index {i} has required argument {arg} set to None"
                    )
        return True

    def _log_evaluation_request(self, data) -> str | None:
        """
        Logs usage to Fi for analytics and creates an evaluation request.
        """
        eval_request_id = None
        try:
            eval_request_id = FiLoggingHelper.create_eval_request(
                eval_name=self.name, request_data={"data": data}, request_type="batch"
            )
        except Exception:
            pass
        return eval_request_id


    def _log_evaluation_results(
        self, eval_request_id: str | None, eval_results: list[EvalResult]
    ):
        """
        Logs the evaluation results to Fi if the eval_request_id is available.
        """
        if eval_request_id:
            try:
                FiLoggingHelper.log_eval_results(
                    eval_request_id=eval_request_id,
                    eval_results=eval_results,
                )
            except Exception:
                pass

    def run(self, **kwargs) -> BatchRunResult:
        """
        Run the LLM evaluator, and log results to Fi.
        """
        logger.info(
            f"base_evaluator_run evaluator={self.name} display_name={self.display_name} input_keys={list(kwargs.keys())}"
        )
        FiApiService.log_usage(eval_name=self.name, run_type="batch")
        eval_request_id = self._log_evaluation_request(kwargs)
        eval_result = self._evaluate(**kwargs)
        # self._log_evaluation_results(
        #     eval_request_id=eval_request_id, eval_results=[eval_result]
        # )

        return BatchRunResult(
            eval_request_id=eval_request_id,
            eval_results=[eval_result],
        )

    def guard(self, max_retries: int = 1, **kwargs):
        """
        Evaluate the input and return a GuardResult.

        When *max_retries* > 1 a reflexion loop is used: if the evaluation
        fails the specific failure *reason* is fed back to the next attempt
        as ``kwargs["feedback"]``, giving the underlying evaluator a chance
        to correct borderline outputs before a hard block is issued.

        Args:
            max_retries: Maximum number of evaluation attempts (default 1,
                         i.e. no retry — original behaviour is preserved).
                         Values above 3 are clamped to 3 to avoid runaway
                         costs on persistent failures.
            **kwargs:    Arguments forwarded to ``_evaluate``.

        Returns:
            GuardResult with *passed*, *reason*, *runtime*, and *attempts*.
        """
        max_retries = max(1, min(max_retries, 3))
        total_runtime = 0

        for attempt in range(1, max_retries + 1):
            eval_result = self._evaluate(**kwargs)
            passed = not eval_result["failure"]
            reason = eval_result["reason"]
            total_runtime += eval_result["runtime"]

            if passed:
                return GuardResult(
                    passed=True,
                    reason=reason,
                    runtime=total_runtime,
                    attempts=attempt,
                )

            # Feed the failure reason back so the next attempt has context
            if attempt < max_retries:
                kwargs["feedback"] = reason

        return GuardResult(
            passed=False,
            reason=reason,
            runtime=total_runtime,
            attempts=max_retries,
        )

    def _run_batch_generator_async(
        self, data: list[DataPoint], max_parallel_evals: int
    ):
        # Wrap _evaluate with OTel context propagation for thread safety
        # This ensures trace context flows from Temporal activity into thread pool workers
        wrapped_evaluate = wrap_for_thread(self._evaluate)

        with ThreadPoolExecutor(max_workers=max_parallel_evals) as executor:
            # Submit all tasks to the executor and store them with their original index
            future_to_index = {
                executor.submit(wrapped_evaluate, **entry): i
                for i, entry in enumerate(data)
            }

            # Create a list to store results in the original order
            results: list[EvalResult | None] = [None] * len(data)

            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as e:
                    entry = data[index]
                    logger.error(f"Error running batch async {entry}: {e}")
                    traceback.print_exc()
                    results[index] = None

            return results

    def _run_batch_generator(self, data: list[DataPoint]):
        """
        Generator function for running a batch of evaluations.
        Iterates over a dataset, and runs the evaluator on each entry.
        """
        for entry in data:
            try:
                yield self._evaluate(**entry)
            except Exception as e:
                logger.error(f"Error evaluating entry {entry}: {e}")
                traceback.print_exc()
                yield None

    def _log_dataset_to_Fi(self, data: list[DataPoint]) -> Dataset | None:
        """
        Logs the dataset to Fi
        """
        try:
            dataset = Dataset.create(
                name=generate_unique_dataset_name(),
                rows=data
            )
            return dataset
        except Exception as e:
            logger.error(f"Error logging dataset to Fi: {e}")
            return None

    def _log_eval_results_to_Fi(self, eval_results: list[EvalResult], dataset_id: str):
        """
        Logs the batch results to Fi
        """
        try:
            eval_config = self.to_config()
            llm_engine = getattr(self, "_model", None)
            FiLoggingHelper.log_eval_results_with_config(
                eval_results_with_config={
                    "eval_results": eval_results,
                    "development_eval_config": {
                        "eval_type_id": self.name,
                        "eval_display_name": generate_eval_display_name(self.display_name),
                        "eval_config": eval_config,
                        "llm_engine": llm_engine
                    }
                },
                dataset_id=dataset_id
            )
        except Exception as e:
            logger.error(f"Error logging eval results to Fi: {e}")
            pass

    def run_batch(
        self, data: list[DataPoint], max_parallel_evals: int = 5, upload_to_fi: bool = True
    ) -> BatchRunResult:
        """
        Runs the evaluator on a batch of data.
        """
        # Log usage to Fi for analytics
        FiApiService.log_usage(eval_name=self.name, run_type="batch")

        # Run the evaluations
        if max_parallel_evals > 1:
            eval_results = self._run_batch_generator_async(data, max_parallel_evals)
        else:
            eval_results = list(self._run_batch_generator(data))

        # Create the Dataset
        if upload_to_fi:
            dataset = self._log_dataset_to_Fi(data)
            if dataset:
                self._log_eval_results_to_Fi(eval_results, dataset.id)
        else:
            logger.warning("Upload to Fi is disabled")

        return BatchRunResult(
            eval_results=eval_results,
        )
