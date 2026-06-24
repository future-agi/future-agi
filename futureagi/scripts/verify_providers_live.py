"""Live provider verification (TH-5642) — run with keys passed via env.

Drives the REAL RetellChatAgentAdapter against the live Retell API, and verifies
the Deepgram + ElevenLabs WebSocket handshakes against the exact payload shapes
our connectors send. Keys are read from env; nothing is printed unmasked.

Usage:
  RETELL_API_KEY=... DEEPGRAM_API_KEY=... ELEVENLABS_API_KEY=... \
    .venv/bin/python scripts/verify_providers_live.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp
import requests

RETELL_BASE = "https://api.retellai.com"
DG_WS = "wss://agent.deepgram.com/v1/agent/converse"
EL_LIST = "https://api.elevenlabs.io/v1/convai/agents"
EL_SIGNED = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"


def mask(s: str) -> str:
    if not s:
        return "<empty>"
    return f"{s[:6]}…{s[-4:]} (len {len(s)})"


def hr(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")


# --------------------------------------------------------------------------
# Retell — drive the REAL adapter end to end.
# --------------------------------------------------------------------------
def verify_retell() -> None:
    hr("RETELL chat adapter (real code, live API)")
    key = os.environ.get("RETELL_API_KEY", "")
    if not key:
        print("SKIP: RETELL_API_KEY not set")
        return
    print(f"key: {mask(key)}")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    # 1) Find a usable agent (prefer an explicit RETELL_TEST_AGENT_ID).
    agent_id = os.environ.get("RETELL_TEST_AGENT_ID", "")
    if not agent_id:
        r = requests.get(f"{RETELL_BASE}/list-agents", headers=headers, timeout=30)
        print(f"list-agents -> {r.status_code}")
        if r.status_code >= 400:
            print(f"FAIL list-agents: {r.text[:300]}")
            return
        agents = r.json() or []
        print(f"found {len(agents)} agents")
        if not agents:
            print("FAIL: no agents in this Retell account to drive")
            return
        # Show shape of the first agent (keys only) to understand chat-capability.
        a0 = agents[0]
        print(f"agent[0] keys: {sorted(a0.keys())}")
        agent_id = a0.get("agent_id") or a0.get("agent_id_")
    print(f"using agent_id: {agent_id}")

    # 2) Drive the REAL adapter.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from simulate.services.retell_chat_agent_adapter import RetellChatAgentAdapter

    adapter = RetellChatAgentAdapter(agent_id=agent_id, api_key=key)
    try:
        # Turn 1: agent speaks first (begin_message from /create-chat).
        t1 = adapter.generate_response([])
        print(f"TURN1 (no user msg): content={t1['content']!r} ended={t1['chat_ended']}")
        print(f"  chat_id cached: {adapter._chat_id}")
        # Turn 2: send a user message, expect an agent reply.
        t2 = adapter.generate_response(
            [{"role": "user", "content": "Hi, what can you help me with?"}]
        )
        print(f"TURN2 content={t2['content']!r:.200} ended={t2['chat_ended']}")
        print(f"  sent_user_turns={adapter._sent_user_turns}")
        # Verdict on the parser assumptions.
        ok = bool(adapter._chat_id) and (t2["content"] or t1["content"])
        print(f"VERDICT: {'PASS' if ok else 'PARTIAL'} — "
              f"adapter created chat + parsed an agent reply via real API")
    except Exception as e:
        print(f"FAIL driving adapter: {type(e).__name__}: {e}")
    finally:
        try:
            adapter.end()
            print("end() called (best-effort cleanup)")
        except Exception as e:
            print(f"end() error: {e}")


# --------------------------------------------------------------------------
# Deepgram — verify the Voice Agent WS handshake with our exact Settings.
# --------------------------------------------------------------------------
async def verify_deepgram() -> None:
    hr("DEEPGRAM Voice Agent WS handshake")
    key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not key:
        print("SKIP: DEEPGRAM_API_KEY not set")
        return
    print(f"key: {mask(key)}")
    settings = {
        "type": "Settings",
        "audio": {
            "input": {"encoding": "linear16", "sample_rate": 16000},
            "output": {"encoding": "linear16", "sample_rate": 16000, "container": "none"},
        },
        "agent": {
            "language": "en",
            "listen": {"provider": {"type": "deepgram", "model": "nova-3"}},
            "think": {"provider": {"type": "open_ai", "model": "gpt-4o-mini"}},
            "speak": {"provider": {"type": "deepgram", "model": "aura-2-thalia-en"}},
        },
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(
                DG_WS, headers={"Authorization": f"Token {key}"}, timeout=20
            ) as ws:
                print("WS connected (auth header accepted)")
                await ws.send_json(settings)
                got = []
                for _ in range(6):
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=8)
                    except asyncio.TimeoutError:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        ev = json.loads(msg.data)
                        got.append(ev.get("type"))
                        if ev.get("type") in ("Welcome", "SettingsApplied"):
                            pass
                        if ev.get("type") == "Error":
                            print(f"  Error event: {msg.data[:300]}")
                            break
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        got.append("BINARY")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                print(f"control frames received: {got}")
                ok = "SettingsApplied" in got or "Welcome" in got
                print(f"VERDICT: {'PASS' if ok else 'PARTIAL'} — "
                      f"handshake {'accepted our Settings' if ok else 'inconclusive'}")
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------
# ElevenLabs — verify signed-url + ConvAI WS handshake.
# --------------------------------------------------------------------------
async def verify_elevenlabs() -> None:
    hr("ELEVENLABS ConvAI signed-url + WS handshake")
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        print("SKIP: ELEVENLABS_API_KEY not set")
        return
    print(f"key: {mask(key)}")
    try:
        async with aiohttp.ClientSession() as s:
            # 1) list agents
            async with s.get(EL_LIST, headers={"xi-api-key": key}, timeout=20) as r:
                print(f"list agents -> {r.status}")
                body = await r.json()
            agents = body.get("agents") if isinstance(body, dict) else body
            agent_id = os.environ.get("ELEVENLABS_TEST_AGENT_ID", "")
            if not agent_id and agents:
                agent_id = agents[0].get("agent_id")
            print(f"agents found: {len(agents) if agents else 0}; using {agent_id}")
            if not agent_id:
                print("PARTIAL: auth worked (listed agents) but none to handshake")
                return
            # 2) signed url
            async with s.get(
                EL_SIGNED, params={"agent_id": agent_id},
                headers={"xi-api-key": key}, timeout=20,
            ) as r:
                print(f"get-signed-url -> {r.status}")
                sj = await r.json()
            signed = sj.get("signed_url")
            if not signed:
                print(f"PARTIAL: no signed_url ({sj})")
                return
            print("signed_url obtained")
            # 3) WS handshake
            async with s.ws_connect(signed, timeout=20) as ws:
                print("WS connected")
                got = []
                for _ in range(4):
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=8)
                    except asyncio.TimeoutError:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        got.append(json.loads(msg.data).get("type"))
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                print(f"frames received: {got}")
                ok = any(g for g in got)
                print(f"VERDICT: {'PASS' if ok else 'PARTIAL'} — "
                      f"{'protocol frames received' if ok else 'no frames'}")
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")


async def main() -> None:
    verify_retell()
    await verify_deepgram()
    await verify_elevenlabs()


if __name__ == "__main__":
    asyncio.run(main())
