"""Bound JSON request bodies before parsing or database work."""

from io import BytesIO
from typing import BinaryIO

from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class BoundedJSONParser(JSONParser):
    max_body_bytes = 10 * 1024 * 1024

    def parse(
        self,
        stream: BinaryIO,
        media_type: str | None = None,
        parser_context: dict | None = None,
    ) -> object:
        body = stream.read(self.max_body_bytes + 1)
        if len(body) > self.max_body_bytes:
            raise ParseError(
                f"JSON request body must be at most {self.max_body_bytes} bytes."
            )
        return super().parse(BytesIO(body), media_type, parser_context)
