"""
Stepper utilities for agent prompt optimizers.

These helpers expose small, serialisable objects that can:
- yield the next prompt candidate
- consume trial results to update best-of state
- export/import state for durable resume

They are intentionally free of Django and heavy deps so they can be stored in
Temporal checkpoints or DB rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from ee.agent_opt.types import IterationHistory

VariationGenerator = Callable[
    [str, int, str, Dict[str, Any], Optional[str], str], List[str]
]


class OptimizerStepper(ABC):
    """Abstract base for resumable prompt optimisers."""

    @classmethod
    @abstractmethod
    def from_state(cls, state: Dict[str, Any], *, config: Dict[str, Any]):
        ...

    @abstractmethod
    def next_candidate(self) -> Optional[str]:
        ...

    @abstractmethod
    def on_result(
        self, *, prompt: str, score: float, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        ...

    @abstractmethod
    def to_state(self) -> Dict[str, Any]:
        ...

    @property
    @abstractmethod
    def remaining(self) -> int:
        ...

    @property
    @abstractmethod
    def best_prompt(self) -> str:
        ...

    @property
    @abstractmethod
    def best_score(self) -> float:
        ...


@dataclass
class RandomSearchStepper(OptimizerStepper):
    """
    Drives RandomSearch one variation at a time.

    The variation list is generated once and stored in state so retries/resumes
    re-evaluate the exact same candidates in the same order.
    """

    initial_prompt: str
    variations: List[str]
    teacher_model: str
    teacher_model_kwargs: Dict[str, Any] = field(default_factory=dict)
    api_key: Optional[str] = None
    current_index: int = 0
    _best_prompt: str = ""
    _best_score: float = -1.0

    @classmethod
    def from_config(
        cls,
        *,
        initial_prompt: str,
        num_variations: int,
        teacher_model: str,
        teacher_model_kwargs: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        variation_generator: VariationGenerator,
        task_description: str = "",
    ) -> "RandomSearchStepper":
        teacher_kwargs = teacher_model_kwargs or {}
        variations = variation_generator(
            initial_prompt, num_variations, teacher_model, teacher_kwargs, api_key, task_description
        )
        return cls(
            initial_prompt=initial_prompt,
            variations=variations,
            teacher_model=teacher_model,
            teacher_model_kwargs=teacher_kwargs,
            api_key=api_key,
            _best_prompt=initial_prompt,
        )

    @classmethod
    def from_state(
        cls,
        state: Dict[str, Any],
        *,
        config: Dict[str, Any],
        variation_generator: VariationGenerator,
        task_description: str = "",
    ) -> "RandomSearchStepper":
        """
        Restore from a serialized state. If variations are missing (should not
        happen), regenerate them to keep the optimiser usable.
        """
        teacher_model = state.get("teacher_model") or config.get("teacher_model")
        teacher_kwargs = state.get("teacher_model_kwargs") or config.get(
            "teacher_model_kwargs", {}
        )
        api_key = config.get("api_key")

        variations = state.get("variations") or []
        if not variations:
            variations = variation_generator(
                state["initial_prompt"],
                config.get("num_variations", 5),
                teacher_model,
                teacher_kwargs,
                api_key,
                task_description,
            )

        return cls(
            initial_prompt=state["initial_prompt"],
            variations=variations,
            teacher_model=teacher_model,
            teacher_model_kwargs=teacher_kwargs,
            api_key=api_key,
            current_index=state.get("current_index", 0),
            _best_prompt=state.get("best_prompt", state.get("initial_prompt", "")),
            _best_score=state.get("best_score", -1.0),
        )

    def next_candidate(self) -> Optional[str]:
        if self.current_index >= len(self.variations):
            return None
        return self.variations[self.current_index]

    def on_result(
        self, *, prompt: str, score: float, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        if score > self._best_score:
            self._best_score = score
            self._best_prompt = prompt
        self.current_index += 1

    def to_state(self) -> Dict[str, Any]:
        return {
            "initial_prompt": self.initial_prompt,
            "variations": self.variations,
            "teacher_model": self.teacher_model,
            "teacher_model_kwargs": self.teacher_model_kwargs,
            "current_index": self.current_index,
            "best_prompt": self._best_prompt,
            "best_score": self._best_score,
        }

    @property
    def remaining(self) -> int:
        return max(0, len(self.variations) - self.current_index)

    @property
    def best_prompt(self) -> str:
        return self._best_prompt

    @property
    def best_score(self) -> float:
        return self._best_score


@dataclass
class MetaPromptStepper(OptimizerStepper):
    """
    Iterative stepper for the MetaPrompt optimiser.

    The stepper keeps track of the current prompt, best prompt, previous
    attempts, and a deterministic evaluation subset to support pause/resume.
    """

    current_prompt: str
    teacher_model: str
    teacher_generate: Callable[[str, Dict[str, Any]], str]
    num_rounds: int
    eval_subset_indices: List[int]
    api_key: Optional[str] = None
    round_index: int = 0
    _best_prompt: str = ""
    _best_score: float = -1.0
    previous_attempts: List[str] = field(default_factory=list)
    meta_prompt_template: str = ""

    @classmethod
    def from_config(
        cls,
        *,
        initial_prompt: str,
        teacher_model: str,
        teacher_generate: Callable[[str, Dict[str, Any]], str],
        num_rounds: int,
        eval_subset_size: int,
        dataset_size: int,
        meta_prompt_template: str,
        api_key: Optional[str] = None,
    ) -> "MetaPromptStepper":
        subset_size = min(eval_subset_size, dataset_size) if dataset_size else 0
        subset_indices = list(range(subset_size))
        return cls(
            current_prompt=initial_prompt,
            teacher_model=teacher_model,
            teacher_generate=teacher_generate,
            num_rounds=num_rounds,
            eval_subset_indices=subset_indices,
            api_key=api_key,
            _best_prompt=initial_prompt,
            meta_prompt_template=meta_prompt_template,
        )

    @classmethod
    def from_state(
        cls,
        state: Dict[str, Any],
        *,
        config: Dict[str, Any],
        dataset_size: int,
        teacher_generate: Callable[[str, Dict[str, Any]], str],
    ) -> "MetaPromptStepper":
        subset_indices = state.get("eval_subset_indices") or list(
            range(min(state.get("eval_subset_size", 0), dataset_size))
        )

        return cls(
            current_prompt=state["current_prompt"],
            teacher_model=state.get("teacher_model") or config.get("teacher_model"),
            teacher_generate=teacher_generate,
            num_rounds=state.get("num_rounds") or config.get("num_rounds", 0),
            eval_subset_indices=subset_indices,
            api_key=config.get("api_key"),
            round_index=state.get("round_index", 0),
            _best_prompt=state.get("best_prompt", state.get("current_prompt", "")),
            _best_score=state.get("best_score", -1.0),
            previous_attempts=state.get("previous_attempts", []),
            meta_prompt_template=state.get("meta_prompt_template")
            or config.get("meta_prompt_template", ""),
        )

    def next_candidate(self) -> Optional[str]:
        if self.round_index >= self.num_rounds:
            return None
        return self.current_prompt

    def on_result(
        self, *, prompt: str, score: float, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        if score > self._best_score:
            self._best_score = score
            self._best_prompt = prompt

        self.previous_attempts.append(prompt)
        self.round_index += 1

        if self.round_index >= self.num_rounds:
            return

        iteration_history: Optional[IterationHistory] = None
        dataset_subset: List[Dict[str, Any]] = []

        if metadata:
            iteration_history = metadata.get("iteration_history")
            dataset_subset = metadata.get("dataset_subset", [])

        if not iteration_history:
            return

        annotated_results = self._format_results(iteration_history, dataset_subset)
        other_attempts = "\n---\n".join(self.previous_attempts[:-1]) or "N/A"

        meta_prompt = self.meta_prompt_template.format(
            current_prompt=prompt,
            other_attempts=other_attempts,
            annotated_results=annotated_results,
            task_description=metadata.get("task_description", "Improve the prompt."),
        )

        generate_kwargs = {"response_format": {"type": "json_object"}}
        if self.api_key:
            generate_kwargs["api_key"] = self.api_key

        improved_prompt = self._generate_improved_prompt(meta_prompt, generate_kwargs)
        if improved_prompt:
            self.current_prompt = improved_prompt

    def to_state(self) -> Dict[str, Any]:
        return {
            "current_prompt": self.current_prompt,
            "teacher_model": self.teacher_model,
            "num_rounds": self.num_rounds,
            "eval_subset_indices": self.eval_subset_indices,
            "round_index": self.round_index,
            "best_prompt": self._best_prompt,
            "best_score": self._best_score,
            "previous_attempts": self.previous_attempts,
            "meta_prompt_template": self.meta_prompt_template,
        }

    @property
    def remaining(self) -> int:
        return max(0, self.num_rounds - self.round_index)

    @property
    def best_prompt(self) -> str:
        return self._best_prompt

    @property
    def best_score(self) -> float:
        return self._best_score

    def subset_for_eval(self, dataset: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.eval_subset_indices:
            return dataset
        return [
            dataset[idx]
            for idx in self.eval_subset_indices
            if 0 <= idx < len(dataset)
        ]

    def _generate_improved_prompt(
        self, meta_prompt: str, generate_kwargs: Dict[str, Any]
    ) -> Optional[str]:
        for attempt in range(3):
            try:
                response = self.teacher_generate(meta_prompt, generate_kwargs)
                parsed = self._parse_output_from_json(response)
                if parsed:
                    return parsed.get("improved_prompt")
            except Exception:
                continue
        return None

    @staticmethod
    def _format_results(
        iteration_history: IterationHistory, dataset: List[Dict[str, Any]]
    ) -> str:
        lines: List[str] = []
        results = iteration_history.individual_results

        for idx, example in enumerate(dataset):
            call_id = example.get("call_execution_id", "unknown")
            result = results.get(call_id)
            if not result:
                continue
            lines.append(f"Example {idx + 1}:")
            lines.append(f"  Input: {example}")
            lines.append(f"  Score: {result.score:.2f}")
            lines.append(f"  Reason: {result.reason}")
            lines.append("---")

        return "\n".join(lines)

    @staticmethod
    def _parse_output_from_json(text: str) -> Optional[Dict[str, Any]]:
        """
        Greedy JSON extraction used to keep compatibility with existing
        MetaPrompt parsing logic while remaining dependency-light.
        """
        import json
        import re

        text = (text or "").strip()

        try:
            return json.loads(text)
        except Exception:
            pass

        try:
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
        except Exception:
            pass

        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
        except Exception:
            return None

        return None


# ---------------------------------------------------------------------------
# Bayesian Search (count-based resume)
# ---------------------------------------------------------------------------


@dataclass
class BayesianStepper(OptimizerStepper):
    n_trials: int
    completed_trials: int = 0
    _best_prompt: str = ""
    _best_score: float = -1.0

    @classmethod
    def from_config(cls, *, n_trials: int, initial_prompt: str) -> "BayesianStepper":
        return cls(n_trials=n_trials, completed_trials=0, _best_prompt=initial_prompt)

    @classmethod
    def from_state(cls, state: Dict[str, Any], *, config: Dict[str, Any]) -> "BayesianStepper":
        return cls(
            n_trials=state.get("n_trials", config.get("n_trials", 0)),
            completed_trials=state.get("completed_trials", 0),
            _best_prompt=state.get("best_prompt", ""),
            _best_score=state.get("best_score", -1.0),
        )

    def next_candidate(self) -> Optional[str]:
        return None  # Optuna drives candidates internally

    def on_result(
        self, *, prompt: str, score: float, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        if score > self._best_score:
            self._best_score = score
            self._best_prompt = prompt
        self.completed_trials += metadata.get("trials_run", 0) if metadata else 0

    def to_state(self) -> Dict[str, Any]:
        return {
            "n_trials": self.n_trials,
            "completed_trials": self.completed_trials,
            "best_prompt": self._best_prompt,
            "best_score": self._best_score,
        }

    @property
    def remaining(self) -> int:
        return max(0, self.n_trials - self.completed_trials)

    @property
    def best_prompt(self) -> str:
        return self._best_prompt

    @property
    def best_score(self) -> float:
        return self._best_score


# ---------------------------------------------------------------------------
# ProTeGi (beam search style)
# ---------------------------------------------------------------------------


@dataclass
class ProTeGiStepper(OptimizerStepper):
    num_rounds: int
    beam: Set[str]
    round_index: int = 0
    _best_prompt: str = ""
    _best_score: float = -1.0

    @classmethod
    def from_config(cls, *, num_rounds: int, initial_prompts: List[str]) -> "ProTeGiStepper":
        base = initial_prompts[0] if initial_prompts else ""
        return cls(num_rounds=num_rounds, beam=set(initial_prompts), _best_prompt=base)

    @classmethod
    def from_state(cls, state: Dict[str, Any], *, config: Dict[str, Any]) -> "ProTeGiStepper":
        return cls(
            num_rounds=state.get("num_rounds", config.get("num_rounds", 0)),
            beam=set(state.get("beam", [])),
            round_index=state.get("round_index", 0),
            _best_prompt=state.get("best_prompt", ""),
            _best_score=state.get("best_score", -1.0),
        )

    def next_candidate(self) -> Optional[str]:
        # Not used; use next_beam instead
        return None

    def next_beam(self) -> Optional[Set[str]]:
        if self.round_index >= self.num_rounds:
            return None
        return set(self.beam)

    def on_result(
        self,
        *,
        prompt: str,
        score: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        new_beam = metadata.get("new_beam") if metadata else None
        if score > self._best_score:
            self._best_score = score
            self._best_prompt = prompt
        if new_beam is not None:
            self.beam = set(new_beam)
        self.round_index += 1

    def to_state(self) -> Dict[str, Any]:
        return {
            "num_rounds": self.num_rounds,
            "beam": list(self.beam),
            "round_index": self.round_index,
            "best_prompt": self._best_prompt,
            "best_score": self._best_score,
        }

    @property
    def remaining(self) -> int:
        return max(0, self.num_rounds - self.round_index)

    @property
    def best_prompt(self) -> str:
        return self._best_prompt

    @property
    def best_score(self) -> float:
        return self._best_score


# ---------------------------------------------------------------------------
# PromptWizard (iterative refinement)
# ---------------------------------------------------------------------------


@dataclass
class PromptWizardStepper(OptimizerStepper):
    refine_iterations: int
    current_best_instruction: str
    iteration_index: int = 0
    _best_prompt: str = ""
    _best_score: float = -1.0

    @classmethod
    def from_config(
        cls, *, refine_iterations: int, initial_prompt: str
    ) -> "PromptWizardStepper":
        return cls(
            refine_iterations=refine_iterations,
            current_best_instruction=initial_prompt,
            _best_prompt=initial_prompt,
        )

    @classmethod
    def from_state(cls, state: Dict[str, Any], *, config: Dict[str, Any]) -> "PromptWizardStepper":
        return cls(
            refine_iterations=state.get(
                "refine_iterations", config.get("refine_iterations", 0)
            ),
            current_best_instruction=state.get("current_best_instruction", ""),
            iteration_index=state.get("iteration_index", 0),
            _best_prompt=state.get("best_prompt", ""),
            _best_score=state.get("best_score", -1.0),
        )

    def next_candidate(self) -> Optional[str]:
        if self.iteration_index >= self.refine_iterations:
            return None
        return self.current_best_instruction

    def on_result(
        self, *, prompt: str, score: float, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        # prompt is the new best instruction for the next iteration
        if score > self._best_score:
            self._best_score = score
            self._best_prompt = prompt
        self.current_best_instruction = prompt
        self.iteration_index += 1

    def to_state(self) -> Dict[str, Any]:
        return {
            "refine_iterations": self.refine_iterations,
            "current_best_instruction": self.current_best_instruction,
            "iteration_index": self.iteration_index,
            "best_prompt": self._best_prompt,
            "best_score": self._best_score,
        }

    @property
    def remaining(self) -> int:
        return max(0, self.refine_iterations - self.iteration_index)

    @property
    def best_prompt(self) -> str:
        return self._best_prompt

    @property
    def best_score(self) -> float:
        return self._best_score
