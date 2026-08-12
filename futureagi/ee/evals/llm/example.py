from dataclasses import dataclass


@dataclass
class FewShotExampleInputParam:
    name: str
    value: str

    def __str__(self) -> str:
        return f"{self.name}: {self.value}"


class FewShotExample:
    """
    Class representing an example of the evaluation that could be used for few-shot prompting.
    """

    # Name of the evaluation function
    input_params: list[FewShotExampleInputParam]
    # Evaluation result - Pass or Fail
    eval_result: str
    # LLM's reason for evaluation
    eval_reason: str

    def __init__(
        self,
        input_params: list[FewShotExampleInputParam],
        eval_result: str,
        eval_reason: str,
    ):
        """
        Initialize a new instance of FewShotExample.
        """
        self.input_params = input_params
        self.eval_result = eval_result
        self.eval_reason = eval_reason

    def __str__(self):
        """
        Return a string representation of the FewShotExample.
        """

        input_params_str = "\n".join([str(param) for param in self.input_params])

        return (
            f"{input_params_str} \n"
            + f"result: {self.eval_result} \n"
            + f"reason:{self.eval_reason} \n"
        )
