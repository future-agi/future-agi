import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ...core.signatures import (
    ImageField,
    ImageListField,
    InputField,
    InputListField,
    OutputField,
)


class SignatureMeta(type(BaseModel)):  # type: ignore[misc]
    def __new__(cls, name, bases, dct):
        # Iterate through class attributes to get docstrings and attach them to fields and methods
        for attr_name, attr_value in dct.items():
            if not attr_name.startswith("__"):  # Exclude dunder methods
                if inspect.isfunction(attr_value) or isinstance(attr_value, property):
                    # If the attribute is a method or property, attach its docstring
                    doc_string = getattr(attr_value, "__doc__", None)
                    if doc_string:
                        attr_value.__doc__ = doc_string
                elif isinstance(attr_value, BaseModel):
                    # If the attribute is a Pydantic model (like InputField, etc.), attach its docstring
                    doc_string = getattr(attr_value, "__doc__", None)
                    if doc_string:
                        dct[attr_name].__doc__ = doc_string

        return super().__new__(cls, name, bases, dct)

    def _validate_fields(cls):
        """Validate fields."""

    def _compare_json_schemas(cls, self, other):
        """Compare the JSON schema of two Pydantic models."""

    def update_field(cls, name: str, value: Any) -> Callable[[Any], Any]:
        """Update the field, name, in a new Signature type.

        Returns a new Signature type with the field, name, updated
        with fields[name].json_schema_extra[key] = value.
        """

        def inner(self):
            new_fields = {**self.__fields__, name: value}
            return type(self)(**new_fields)

        return inner


class Signature(BaseModel, metaclass=SignatureMeta):
    pass


def format_signature(signature: Signature):
    input_fields = []
    input_list_fields = []
    image_fields = []
    image_list_fields = []
    output_fields = []
    output_list_fields = []

    for name, field in signature.__fields__.items():
        if field.json_schema_extra.get("tfc_field_type") == "input":
            input_fields.append(name)
        elif field.json_schema_extra.get("tfc_field_type") == "input_list":
            input_list_fields.append(name)
        elif field.json_schema_extra.get("tfc_field_type") == "image_list":
            image_list_fields.append(name)
        elif field.json_schema_extra.get("tfc_field_type") == "image":
            image_fields.append(name)
        elif field.json_schema_extra.get("tfc_field_type") == "output_list":
            output_list_fields.append(name)
        elif field.json_schema_extra.get("tfc_field_type") == "output":
            output_fields.append(name)

    return (
        input_fields,
        input_list_fields,
        image_fields,
        image_list_fields,
        output_fields,
        output_list_fields,
    )


def update_signature(signature: Signature, kwargs):
    (
        input_fields,
        input_list_fields,
        image_fields,
        image_list_fields,
        output_fields,
        output_list_fields,
    ) = format_signature(signature)

    # Update fields based on input lists
    for field_name in input_fields:
        if field_name in kwargs:
            setattr(signature, field_name, kwargs[field_name])

    for field_name in input_list_fields:
        if field_name in kwargs:
            setattr(signature, field_name, kwargs[field_name])

    for field_name in image_fields:
        if field_name in kwargs:
            setattr(signature, field_name, kwargs[field_name])

    for field_name in image_list_fields:
        if field_name in kwargs:
            setattr(signature, field_name, kwargs[field_name])

    for field_name in output_fields:
        if field_name in kwargs:
            setattr(signature, field_name, kwargs[field_name])

    for field_name in output_list_fields:
        if field_name in kwargs:
            setattr(signature, field_name, kwargs[field_name])
    return signature


def get_signature_object(signature: Signature):
    final_obj = {}
    for key, value in signature.model_fields.items():
        if value.json_schema_extra.get("tfc_field_type") == "input":
            if final_obj.get("input"):
                final_obj["input"].append(
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                )
            else:
                final_obj["input"] = [
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                ]
        if value.json_schema_extra.get("tfc_field_type") == "image":
            if final_obj.get("image"):
                final_obj["image"].append(
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                )
            else:
                final_obj["image"] = [
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                ]
        if value.json_schema_extra.get("tfc_field_type") == "image_list":
            if final_obj.get("image"):
                final_obj["image"].append(
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                )
            else:
                final_obj["image"] = [
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                ]
        if value.json_schema_extra.get("tfc_field_type") == "input_list":
            if final_obj.get("input"):
                final_obj["input"].append(
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                )
            else:
                final_obj["input"] = [
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                ]
        if value.json_schema_extra.get("tfc_field_type") == "output":
            if final_obj.get("output"):
                final_obj["output"].append(
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                )
            else:
                final_obj["output"] = [
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                ]
        if value.json_schema_extra.get("tfc_field_type") == "output_list":
            if final_obj.get("output"):
                final_obj["output"].append(
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                )
            else:
                final_obj["output"] = [
                    {
                        "desc": value.json_schema_extra.get("desc"),
                        key: getattr(signature, key),
                    }
                ]
    if signature.__doc__:
        final_obj["doc"] = signature.__doc__.strip()
    return final_obj


# Usage example
class MySignature(Signature):
    """
    instruction to follow.
    """

    a: Any = InputField(desc="This is an input field")
    b: list[Any] = InputListField(desc="This is an input list field")
    il: list[Any] = ImageListField(desc="This is an image list field")
    i: Any = ImageField(desc="This is an imagefield")
    o: Any = OutputField(desc="This is an output")


class ChainOfThought:
    def __init__(self, signature: Signature):
        self.signature = signature

    def process(self, **kwargs):
        (
            input_fields,
            input_list_fields,
            image_fields,
            image_list_fields,
            output_fields,
            output_list_fields,
        ) = format_signature(self.signature)
        # result = {}

        return update_signature(self.signature, kwargs)
