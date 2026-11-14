import json
import logging
import time
from typing import Any, Optional

from integrations.services.base import BaseIntegrationService, register_service

logger = logging.getLogger(__name__)

# Sub-platform constants
SQS = "sqs"
PUBSUB = "pubsub"


class QueueService(BaseIntegrationService):
    """Message queue client for real-time log streaming (SQS, Pub/Sub).

    Credentials dict shape varies by sub_platform:
        SQS:
            sub_platform: "sqs"
            queue_url (str): SQS queue URL
            region (str): AWS region
            access_key_id (str): AWS access key
            secret_access_key (str): AWS secret key
        Pub/Sub:
            sub_platform: "pubsub"
            topic_path (str): Full topic path (projects/{project}/topics/{topic})
            project_id (str): GCP project ID
            service_account_json (str): JSON string of service account key
    """

    TIMEOUT = 15

    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: Optional[str] = None,
    ) -> dict:
        sub_platform = credentials.get("sub_platform", "")
        if not sub_platform:
            return {"valid": False, "error": "Queue type is required (sqs or pubsub)."}

        try:
            if sub_platform == SQS:
                return self._validate_sqs(credentials)
            elif sub_platform == PUBSUB:
                return self._validate_pubsub(credentials)
            else:
                return {"valid": False, "error": f"Unknown queue type: {sub_platform}"}
        except Exception as e:
            logger.exception("Error validating queue credentials")
            return {"valid": False, "error": f"Validation failed: {str(e)}"}

    def _validate_sqs(self, credentials: dict) -> dict:
        queue_url = credentials.get("queue_url", "")
        region = credentials.get("region", "us-east-1")
        access_key = credentials.get("access_key_id", "")
        secret_key = credentials.get("secret_access_key", "")

        if not queue_url:
            return {"valid": False, "error": "SQS queue URL is required."}
        if not access_key or not secret_key:
            return {
                "valid": False,
                "error": "AWS access key and secret key are required.",
            }

        try:
            import boto3
            from botocore.exceptions import ClientError

            client = boto3.client(
                "sqs",
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )

            # Test connectivity by getting queue attributes
            client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=["QueueArn"],
            )

            return {"valid": True, "projects": [], "total_traces": 0}

        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"]["Message"]
            return {"valid": False, "error": f"SQS error ({code}): {msg}"}
        except ImportError:
            return {"valid": False, "error": "boto3 is not installed on the server."}

    def _validate_pubsub(self, credentials: dict) -> dict:
        topic_path = credentials.get("topic_path", "")
        sa_json = credentials.get("service_account_json", "")

        if not topic_path:
            return {"valid": False, "error": "Pub/Sub topic path is required."}
        if not sa_json:
            return {"valid": False, "error": "Service account JSON is required."}

        try:
            from google.cloud import pubsub_v1
            from google.oauth2 import service_account

            info = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(info)
            publisher = pubsub_v1.PublisherClient(credentials=creds)

            # Test connectivity by getting the topic
            publisher.get_topic(request={"topic": topic_path})

            return {"valid": True, "projects": [], "total_traces": 0}

        except ImportError:
            return {
                "valid": False,
                "error": "google-cloud-pubsub is not installed on the server.",
            }
        except json.JSONDecodeError:
            return {"valid": False, "error": "Invalid service account JSON."}
        except Exception as e:
            return {"valid": False, "error": f"Pub/Sub error: {str(e)}"}

    def fetch_traces(
        self,
        host_url: str,
        credentials: dict,
        from_timestamp: Optional[str] = None,
        to_timestamp: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
        ca_certificate: Optional[str] = None,
    ) -> dict:
        """Not used — message queue is an export target."""
        return {"traces": [], "has_more": False, "next_page": 1, "total_items": 0}

    def fetch_trace_detail(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: Optional[str] = None,
    ) -> dict[str, Any]:
        """Not used — message queue is an export target."""
        return {}

    # --- Publish methods (called by sync tasks) ---

    def publish_logs(self, credentials: dict, logs: list[dict]) -> dict:
        """Publish log events to a message queue.

        Args:
            credentials: Queue credentials with sub_platform key.
            logs: List of log dicts to publish.

        Returns:
            dict with count of messages published.
        """
        if not logs:
            return {"sent": 0}

        sub_platform = credentials.get("sub_platform", "")
        if sub_platform == SQS:
            return self._publish_sqs(credentials, logs)
        elif sub_platform == PUBSUB:
            return self._publish_pubsub(credentials, logs)
        return {"sent": 0, "error": f"Unknown queue type: {sub_platform}"}

    def _publish_sqs(self, credentials: dict, logs: list[dict]) -> dict:
        """Require every acknowledgement; retry only rejected transient entries.

        Delivery remains at-least-once: a transport timeout or a later sync retry
        can replay accepted messages. Consumers should deduplicate by request_id
        from the unchanged message body, not the batch-local SQS entry Id.
        """
        import boto3

        client = boto3.client(
            "sqs",
            region_name=credentials.get("region", "us-east-1"),
            aws_access_key_id=credentials["access_key_id"],
            aws_secret_access_key=credentials["secret_access_key"],
        )

        queue_url = credentials["queue_url"]
        sent = 0

        # SQS send_message_batch supports max 10 messages
        for i in range(0, len(logs), 10):
            batch = logs[i : i + 10]
            entries = []
            for idx, log in enumerate(batch):
                entries.append(
                    {
                        "Id": str(idx),
                        "MessageBody": json.dumps(log),
                        "MessageAttributes": {
                            "source": {
                                "StringValue": "agentcc-gateway",
                                "DataType": "String",
                            },
                            "event_type": {
                                "StringValue": log.get("event_type", "request"),
                                "DataType": "String",
                            },
                        },
                    }
                )

            for attempt in range(4):
                resp = client.send_message_batch(QueueUrl=queue_url, Entries=entries)
                successful = resp.get("Successful", [])
                failed = resp.get("Failed", [])
                expected_ids = {entry["Id"] for entry in entries}
                response_ids = [entry.get("Id") for entry in successful + failed]
                if set(response_ids) != expected_ids or len(response_ids) != len(
                    expected_ids
                ):
                    raise RuntimeError("SQS returned incomplete batch acknowledgements")

                sent += len(successful)
                if not failed:
                    break

                # HTTP 200 does not mean every entry was delivered. Never let a
                # partial batch advance the sync checkpoint, or resend successes
                # during these entry-level retries.
                if (
                    any(entry.get("SenderFault", True) for entry in failed)
                    or attempt == 3
                ):
                    codes = sorted({entry.get("Code", "Unknown") for entry in failed})
                    raise RuntimeError(
                        f"SQS batch delivery failed for {len(failed)} entries "
                        f"({', '.join(codes)})"
                    )
                failed_ids = {entry["Id"] for entry in failed}
                entries = [entry for entry in entries if entry["Id"] in failed_ids]
                time.sleep(0.25 * (2**attempt))

        return {"sent": sent}

    def _publish_pubsub(self, credentials: dict, logs: list[dict]) -> dict:
        from google.cloud import pubsub_v1
        from google.oauth2 import service_account

        info = json.loads(credentials["service_account_json"])
        creds = service_account.Credentials.from_service_account_info(info)
        publisher = pubsub_v1.PublisherClient(credentials=creds)
        topic_path = credentials["topic_path"]

        futures = []
        for log in logs:
            data = json.dumps(log).encode()
            future = publisher.publish(
                topic_path,
                data=data,
                source="agentcc-gateway",
                event_type=log.get("event_type", "request"),
            )
            futures.append(future)

        # Wait for all publishes
        sent = 0
        for future in futures:
            future.result()
            sent += 1

        return {"sent": sent}


# Self-register on module import
_queue_service = QueueService()
register_service("message_queue", _queue_service)
