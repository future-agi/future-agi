from __future__ import annotations

import asyncio
import json
import os
import uuid

from livekit import api
from livekit.protocol.sip import ListSIPDispatchRuleRequest


async def main() -> None:
    simulator_client = _client(
        url_env="ACCEPTANCE_LIVEKIT_URL",
        api_key_env="LIVEKIT_API_KEY",
        api_secret_env="LIVEKIT_API_SECRET",
    )
    target_client = _client(
        url_env="LIVEKIT_TARGET_URL",
        api_key_env="LIVEKIT_TARGET_API_KEY",
        api_secret_env="LIVEKIT_TARGET_API_SECRET",
        fallback=simulator_client,
    )
    origin_room = f"acceptance-origin-{uuid.uuid4().hex[:12]}"
    origin_room_created = False
    try:
        target_room = await _wait_for_target_room(simulator_client)
        await target_client.room.create_room(api.CreateRoomRequest(name=origin_room))
        origin_room_created = True
        await target_client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=os.environ["LIVEKIT_TARGET_AGENT_NAME"],
                room=origin_room,
                metadata=json.dumps(
                    {
                        "target_instructions": os.environ[
                            "LIVEKIT_TARGET_SYSTEM_PROMPT"
                        ],
                        "outbound_sip_trunk_id": os.environ[
                            "LIVEKIT_OUTBOUND_TRUNK_ID"
                        ],
                        "outbound_sip_number": os.environ["PSTN_CALLER_NUMBER"],
                        "outbound_sip_call_to": os.environ["LIVEKIT_INBOUND_DID"],
                        "outbound_sip_participant_identity": (
                            "livekit-originating-target"
                        ),
                    },
                    sort_keys=True,
                ),
            )
        )
        await _wait_for_target_cleanup(simulator_client, target_room)
    finally:
        try:
            if origin_room_created:
                try:
                    await target_client.room.delete_room(
                        api.DeleteRoomRequest(room=origin_room)
                    )
                except Exception as exc:  # noqa: BLE001
                    code = getattr(exc, "code", None)
                    if getattr(code, "value", code) != "not_found":
                        raise
        finally:
            await target_client.aclose()
            if target_client is not simulator_client:
                await simulator_client.aclose()


def _client(
    *,
    url_env: str,
    api_key_env: str,
    api_secret_env: str,
    fallback: api.LiveKitAPI | None = None,
) -> api.LiveKitAPI:
    url = os.environ.get(url_env, "")
    api_key = os.environ.get(api_key_env, "")
    api_secret = os.environ.get(api_secret_env, "")
    if not any((url, api_key, api_secret)):
        if fallback is None:
            raise RuntimeError(f"{url_env.lower()}_missing")
        return fallback
    if not all((url, api_key, api_secret)):
        raise RuntimeError(f"{url_env.lower()}_credentials_incomplete")
    return api.LiveKitAPI(
        url=_api_url(url),
        api_key=api_key,
        api_secret=api_secret,
    )


async def _wait_for_target_room(client: api.LiveKitAPI) -> str:
    override = os.environ.get("ACCEPTANCE_ROOM_NAME_OVERRIDE", "").strip()
    if override:
        return override
    for _ in range(240):
        response = await client.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
        for item in response.items:
            direct = getattr(item.rule, "dispatch_rule_direct", None)
            room_name = getattr(direct, "room_name", "") if direct else ""
            if item.name.startswith("sim-inbound-") and room_name.startswith(
                "acceptance-1-2-1-"
            ):
                return room_name
        await asyncio.sleep(0.5)
    raise TimeoutError("livekit_outbound_target_room_not_ready")


async def _wait_for_target_cleanup(
    client: api.LiveKitAPI,
    target_room: str,
) -> None:
    for _ in range(400):
        response = await client.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
        if not any(
            getattr(
                getattr(item.rule, "dispatch_rule_direct", None),
                "room_name",
                "",
            )
            == target_room
            for item in response.items
        ):
            return
        await asyncio.sleep(0.5)


def _api_url(url: str) -> str:
    if url.startswith("wss://"):
        return f"https://{url.removeprefix('wss://')}"
    if url.startswith("ws://"):
        return f"http://{url.removeprefix('ws://')}"
    return url


if __name__ == "__main__":
    asyncio.run(main())
