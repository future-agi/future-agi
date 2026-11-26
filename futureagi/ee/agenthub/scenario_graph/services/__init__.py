"""Graph scenario services — focused, stateless functions extracted from EnhancedScenariosAgent.

Each service module handles a single responsibility. Services accept explicit
arguments (typically agent_context dicts) and return plain data. No shared state.
"""

from .branch_metadata import (
    create_branch_metadata_dict,
    create_branch_metadata_strings,
    create_conversation_flow_description,
    extract_conversation_flow,
    generate_branch_description,
)
from .branch_selector import select_branches
from .case_generator import generate_cases_for_intent
from .case_transformer import (
    convert_sda_data_to_cases,
    enrich_cases_with_branch_data,
    normalize_cases,
    sample_cases_across_branches,
)
from .category_service import categorize_branch
from .dataset_persister import create_scenario_dataset
from .llm_factory import create_llm
from .payload_builder import build_sda_payload
from .persona_validator import validate_persona

__all__ = [
    # branch_metadata
    "create_branch_metadata_dict",
    "create_branch_metadata_strings",
    "create_conversation_flow_description",
    "extract_conversation_flow",
    "generate_branch_description",
    # branch_selector
    "select_branches",
    # case_generator
    "generate_cases_for_intent",
    # case_transformer
    "convert_sda_data_to_cases",
    "enrich_cases_with_branch_data",
    "normalize_cases",
    "sample_cases_across_branches",
    # category_service
    "categorize_branch",
    # dataset_persister
    "create_scenario_dataset",
    # llm_factory
    "create_llm",
    # payload_builder
    "build_sda_payload",
    # persona_validator
    "validate_persona",
]
