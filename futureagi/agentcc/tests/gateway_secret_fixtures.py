"""Synthetic secret-slot fixture shared by migration and key-transition rehearsals.

No URL is contacted. Include both spellings simultaneously: stored configs need
not have been canonicalized before the historical backfills.
"""

import copy
import json

from cryptography.fernet import Fernet


def encrypted_sections(source):
    from agentcc.services.org_config_secrets import iter_secret_slots

    # JSON storage has no shared container references between alias spellings.
    sections = json.loads(json.dumps(secret_sections()))
    fernet = Fernet(source.encode())
    for slot in iter_secret_slots(sections):
        slot.set(
            "enc::" + fernet.encrypt(json.dumps({"v": slot.value}).encode()).decode()
        )
    return sections


def secret_sections():
    secret = "synthetic-transition-value"
    azure = dict.fromkeys(
        ("connection_string", "connectionString", "sas_token", "sasToken"), secret
    )
    auth = {"token": secret, "key": secret}
    credential = {"secret": {"value": secret}}
    return copy.deepcopy(
        {
            "cache": {
                "enabled": False,
                "redis": {"address": "127.0.0.1:9", "password": secret},
                "s3": {"secret_access_key": secret, "secretAccessKey": secret},
                "azure_blob": azure,
                "azureBlob": azure,
                "semantic": {
                    backend: {
                        "url": "https://sink.invalid",
                        "api_key": secret,
                        "apiKey": secret,
                    }
                    for backend in ("qdrant", "weaviate", "pinecone")
                },
            },
            "a2a": {"agents": [{"url": "https://sink.invalid", "auth": auth}]},
            "audit": {
                "sinks": [
                    {
                        "url": "https://sink.invalid",
                        "headers": {"Authorization": secret},
                    }
                ]
            },
            "alerting": {
                "channels": [
                    {
                        "name": "slack",
                        "type": "slack",
                        "url": "https://sink.invalid/hooks/Synthetic1",
                        "headers": {"Authorization": secret},
                    },
                    {
                        "name": "plain",
                        "type": "webhook",
                        "url": "https://sink.invalid/hooks/on-call",
                    },
                    {
                        "name": "query",
                        "type": "webhook",
                        "url": "https://sink.invalid/hook?token=synthetic",
                    },
                ]
            },
            "mcp": {
                "servers": [
                    {
                        "url": "https://sink.invalid",
                        "auth": auth,
                        "credential": credential,
                    }
                ],
                "managed_tools": {"credentials": {"fixture": credential}},
                "managedTools": {"credentials": {"fixture": credential}},
            },
        }
    )
