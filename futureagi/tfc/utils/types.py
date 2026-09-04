import json
import uuid
from datetime import datetime
from enum import Enum, unique
from typing import Literal, TypedDict


@unique
class ProcessStatus(Enum):
    NOT_STARTED = 1
    PROCESSING = 2
    COMPLETE = 3


@unique
class EventTypes(Enum):
    MODEL_PREDICTION = 1
    EVENT = 2


@unique
class JourneyPropertyType(Enum):
    EVENT = "EVENT"
    BREAKDOWN = "BREAKDOWN"
    FILTER = "FILTER"


@unique
class ClickhouseDatatypes(Enum):
    STRING = "String"
    INTEGER = "Integer"
    FLOAT = "Float"
    BOOLEAN = "Boolean"
    DATE = "Date"
    JSON = "JSON"
    UUID = "UUID"
    LIST = "LIST"
    # Add other data types as needed

    @staticmethod
    def get_data_type(value):
        if isinstance(value, str):
            return ClickhouseDatatypes.STRING
        elif isinstance(value, bool):
            # bool is a subclass of int, so this must be checked before int,
            # otherwise every boolean is classified as INTEGER.
            return ClickhouseDatatypes.BOOLEAN
        elif isinstance(value, int):
            return ClickhouseDatatypes.INTEGER
        elif isinstance(value, float):
            return ClickhouseDatatypes.FLOAT
        elif isinstance(value, datetime):
            return ClickhouseDatatypes.DATE
        elif isinstance(value, list):
            return ClickhouseDatatypes.LIST
        elif isinstance(value, dict):  # Assuming JSON is represented as a Python dict
            return ClickhouseDatatypes.JSON
        elif isinstance(
            value, uuid.UUID
        ):  # Assuming JSON is represented as a Python dict
            return ClickhouseDatatypes.UUID
        else:
            return None  # Or handle unknown data types differently

    @staticmethod
    def convert_to_type(value_str, data_type):
        if data_type == ClickhouseDatatypes.STRING:
            return value_str
        elif data_type == ClickhouseDatatypes.INTEGER:
            return int(value_str)
        elif data_type == ClickhouseDatatypes.FLOAT:
            return float(value_str)
        elif data_type == ClickhouseDatatypes.BOOLEAN:
            return value_str.lower() in ["true", "1", "t", "y", "yes"]
        elif data_type == ClickhouseDatatypes.DATE:
            # Assuming the date format is YYYY-MM-DD, adjust as needed
            return datetime.strptime(value_str, "%Y-%m-%d").date()
        elif data_type == ClickhouseDatatypes.JSON:
            return json.loads(value_str)
        else:
            return None  # Or handle unknown data types differently


@unique
class Environments(Enum):
    TRAINING = 1
    VALIDATION = 2
    PRODUCTION = 3
    CORPUS = 4

    @staticmethod
    def get_data_type(value):
        if value == Environments.TRAINING.value:
            return "Training"
        if value == Environments.VALIDATION.value:
            return "Validation"
        if value == Environments.PRODUCTION.value:
            return "Production"
        if value == Environments.CORPUS.value:
            return "Corpus"

    @staticmethod
    def convert_to_type(val):
        if val == "Training":
            return Environments.TRAINING.value
        if val == "Validation":
            return Environments.VALIDATION.value
        if val == "Production":
            return Environments.PRODUCTION.value
        if val == "Corpus":
            return Environments.CORPUS.value


# types for connector SourceConfig
class SourceConfig(TypedDict):
    definitions_name: Literal["UploadFile", "BigQuery", "MongoDB", "PostgreSQL"] | None
    table_id: str | None
    connection_id: str | None
    auth_source: str | None
    cluster_type: str | None
    connection_string: str | None
    password: str | None
    username: str | None
    database: str | None
    update_capture_mode: str | None
    invalid_cdc_cursor_position_behavior: str | None
    credentials_json: dict | None


# types for connector ConnMappings
class DataPointsConnMapping(TypedDict):
    conversation_id: str
    timestamp: str
    context: str | None
    prompt_template: str | None
    variables: str | None
    prompt: list[str] | None
    response: list[str] | None


class KnowledgeBaseConnMapping(TypedDict):
    text_info: str | None
