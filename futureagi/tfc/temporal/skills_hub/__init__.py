"""Native Skills orchestration on the existing tasks_l queue."""


def get_workflows() -> list:
    from .workflows import SkillsJobWorkflow

    return [SkillsJobWorkflow]


def get_activities() -> list:
    from .activities import (
        skills_execute_attempt,
        skills_execute_once,
        skills_finish_job,
        skills_start_job,
    )

    return [
        skills_start_job,
        skills_execute_attempt,
        skills_execute_once,
        skills_finish_job,
    ]
