from agent_playground.templates._registry import TemplateDefinition, register_template

HTTP_REQUEST_TEMPLATE: TemplateDefinition = {
    "name": "http_request",
    "display_name": "HTTP Request",
    "description": (
        "Make an HTTP request to an external API. Supports GET, POST, PUT, PATCH, "
        "and DELETE methods with configurable headers, body, authentication, timeout, "
        "and retries. Input ports are dynamic — auto-generated from {{variable}} "
        "placeholders in the URL, headers, and body."
    ),
    "icon": None,
    "categories": ["http", "api", "integration"],
    "input_definition": [],
    "output_definition": [
        {
            "key": "response",
            "data_schema": {},
        }
    ],
    "input_mode": "dynamic",
    "output_mode": "strict",
    "config_schema": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
            },
            "url": {"type": "string", "minLength": 1},
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "body": {"type": ["string", "object", "null"]},
            "auth": {
                "type": ["object", "null"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["none", "bearer", "basic"],
                    },
                    "token": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                },
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 300,
                "default": 30,
            },
            "retries": {
                "type": "integer",
                "minimum": 0,
                "maximum": 5,
                "default": 0,
            },
        },
        "required": ["method", "url"],
        "additionalProperties": False,
    },
}

register_template(HTTP_REQUEST_TEMPLATE)
