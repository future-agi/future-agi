"""Synthetic-fixture application: real Deep Agents graph/checkpoints, fake model only.

Invoked on the owned cloud VM. All context writes/reads use the public SDK. The
model boundary records actual messages from the pinned runtime, not API resolve
responses or a fabricated delivery receipt. Private credential/checkpoint state
stays beside the input and must never be fetched with redacted evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from fi.platform import Client, ContextAuth, RequestOptions
from fi.context.sessions import ContextSessionClient


def save(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["setup", "initial", "fresh", "resume", "follow", "cleanup"])
    parser.add_argument("input", type=Path)
    parser.add_argument("index", type=int, choices=range(3))
    args = parser.parse_args()
    config = json.loads(args.input.read_text())
    parent = config["parents"][args.index]
    state_path = args.input.parent / f"runtime-{args.index}.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    actor = config["sdkActor"]
    if args.operation in {"setup", "follow", "cleanup"}:
        with Client(api_key=actor["apiKey"], secret_key=actor["secretKey"],
                    base_url=config["apiUrl"], workspace_id=actor["workspaceId"],
                    default_headers={"X-Organization-Id": actor["organizationId"]}, max_retries=0) as api:
            root = "/model-hub/skills/contexts/"
            if args.operation == "setup":
                binding = api.request("POST", root + parent["id"] + "/bindings/", body={
                    "name": f"{parent['name']}-runtime", "kind": "runtime", "selector": {"version": 1},
                    "update_mode": "new_thread", "profile": "deepagents", "profile_version": "0.4.11",
                    "repository": None, "expected_report_interval_seconds": 3600},
                    options=RequestOptions(idempotency_key=str(uuid4())))
                version = api.request("GET", root + parent["id"] + "/versions/1/inspection/")["manifest"]
                credential = api.request("POST", root + "credentials/", body={
                    "purpose": "consume", "grants": [{"kind": "agent_context", "resource_id": parent["id"],
                    "labels": ["production"], "version_ids": [version["version_id"]]}], "binding_ids": [],
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()},
                    options=RequestOptions(idempotency_key=str(uuid4())))
                # The application reports what its model actually received.
                reporter = api.request("POST", root + "credentials/", body={
                    "purpose": "report", "grants": [], "binding_ids": [binding["id"]],
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()},
                    options=RequestOptions(idempotency_key=str(uuid4())))
                state = {"binding": binding, "credential": credential, "reporter": reporter, "sequence": 0}
                save(state_path, state)
                print(json.dumps({"binding": binding["id"], "credential_id": credential["id"]}))
            elif args.operation == "follow":
                binding = state["binding"]
                body = {key: binding[key] for key in ("name", "kind", "selector", "update_mode", "profile", "profile_version", "repository", "expected_report_interval_seconds")}
                body["selector"] = {"label": "production"}
                state["binding"] = api.request("PATCH", root + "bindings/" + binding["id"] + "/",
                    body={"revision": binding["revision"], "binding": body})
                save(state_path, state)
                print(json.dumps({"binding": state["binding"]["id"], "revision": state["binding"]["revision"]}))
            else:
                # Remove every owned runtime resource and verify it is gone.
                for key in ("credential", "reporter"):
                    if key in state:
                        api.request("DELETE", root + "credentials/" + state[key]["id"] + "/")
                for binding in [state["binding"], *state.get("unrelated_bindings", [])]:
                    api.request("DELETE", root + "bindings/" + binding["id"] + "/", body={"revision": binding["revision"]})
                remaining = {row["id"] for row in api.request("GET", root + parent["id"] + "/bindings/").get("results", [])}
                assert state["binding"]["id"] not in remaining, "Runtime binding was not destroyed"
                print(json.dumps({"revoked": True, "bindings_removed": True}))
        return

    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.sqlite import SqliteSaver
    from fi.context.adapters.deepagents import mount_context, MEMORY_SOURCE, SKILLS_SOURCE

    with Client(auth=ContextAuth(state["credential"]["secret"]), base_url=config["apiUrl"], max_retries=0) as api:
        sessions = ContextSessionClient(api, context_id=parent["id"], binding_id=state["binding"]["id"])
        if args.operation == "resume":
            request = {**state["initial_request"], "resume_session_id": state["initial_session"]["session_id"]}
        else:
            request = {"client_session_key": str(uuid4()), "incarnation_id": str(uuid4()),
                "resume_session_id": None, "adapter_version": "j3-synthetic-1", "runtime_version": "deepagents-0.4.11",
                "runtime_config_digest": "sha256:" + hashlib.sha256(b"deepagents-0.4.11/readonly-context/sqlite/fake-model").hexdigest()}
        session = sessions.open(request, idempotency_key=str(uuid4()))
        if args.operation == "resume":
            assert session["version_id"] == state["initial_session"]["version_id"]
            assert session["digest"] == state["initial_session"]["digest"]
        snapshot = sessions.snapshot(session)
        verified = snapshot.read_all()
        boundary = []
        path = f"/fi_context/skills/{config['leaves'][0]['name']}/SKILL.md"

        class BoundaryModel(BaseChatModel):
            @property
            def _llm_type(self):
                return "synthetic-j3-transport"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                sessions.check_access(session, snapshot)
                boundary.append([message.model_dump(mode="json") for message in messages])
                last_human = max(i for i, message in enumerate(messages) if isinstance(message, HumanMessage))
                delivered = [message for message in messages[last_human + 1:] if isinstance(message, ToolMessage)]
                answer = AIMessage(content="synthetic-approved") if delivered else AIMessage(content="", tool_calls=[{
                    "id": str(uuid4()), "name": "read_file", "args": {"file_path": path}}])
                return ChatResult(generations=[ChatGeneration(message=answer)])

        def backend(runtime):
            return mount_context(verified, default=StateBackend(runtime),
                authorize=lambda identity, lease: sessions.check_access(session, snapshot))

        with SqliteSaver.from_conn_string(str(args.input.parent / f"checkpoint-{args.index}.sqlite")) as checkpoints:
            graph = create_deep_agent(model=BoundaryModel(), backend=backend, memory=[MEMORY_SOURCE],
                skills=[SKILLS_SOURCE], checkpointer=checkpoints)
            invocation = {"configurable": {"thread_id": request["client_session_key"]}}
            before = graph.get_state(invocation)
            if args.operation == "resume":
                assert before.values.get("messages"), "The actual runtime checkpoint was lost"
            else:
                assert not before.values.get("messages"), "Fresh thread reused checkpoint history"
            graph.invoke({"messages": [{"role": "user", "content": "Explain the approved purchase policy; consult its linked skill."}]}, invocation)
            after = graph.get_state(invocation)
            assert after.values.get("messages")
        # Report standing-instruction delivery only when the actual native model
        # input contained the complete AGENTS.md of this exact session version.
        agents = verified.files["AGENTS.md"]
        def text_of(content):
            # Native messages carry either a string or a list of content blocks.
            if isinstance(content, str):
                return content
            return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content or [])
        system_text = "\n".join(text_of(message.get("content")) for row in boundary for message in row if message.get("type") == "system")
        assert agents.strip() and agents in system_text, "Standing root absent at the native model boundary"
        sha = next(item.sha256 for item in verified.manifest.files if item.path == "AGENTS.md")
        state["sequence"] = state.get("sequence", 0) + 1
        with Client(auth=ContextAuth(state["reporter"]["secret"]), base_url=config["apiUrl"], max_retries=0) as reporter:
            receipt = reporter.request("POST", f"/model-hub/skills/contexts/bindings/{state['binding']['id']}/receipts/", body={
                "event_id": str(uuid4()), "session_id": session["session_id"], "instance_id": request["incarnation_id"],
                "sequence": state["sequence"], "stage": "instructions_delivered", "version_id": session["version_id"],
                "digest": session["digest"], "materialization_digest": None, "observed_at": datetime.now(timezone.utc).isoformat(),
                "details": {"path": "AGENTS.md", "sha256": sha, "ranges": [[0, len(agents)]], "runtime_version": "deepagents-0.4.11",
                    "adapter_version": "j3-synthetic-1", "diagnostic_codes": [], "trace_ref": None}})
        if args.operation == "initial":
            state.update(initial_request=request, initial_session=session)
        save(state_path, state)
        evidence = {"evidence_class": "synthetic fixture", "runtime": "deepagents-0.4.11", "mode": args.operation,
            "session_id": session["session_id"], "version_id": session["version_id"], "digest": session["digest"],
            "had_checkpoint": bool(before.values.get("messages")), "boundary": boundary, "receipt": receipt["id"],
            "descriptor": json.loads(verified.files[".futureagi/context.json"])}
        directory = Path(config["outputDir"]) / "runtime"
        directory.mkdir(parents=True, exist_ok=True)
        save(directory / f"{args.index}-{args.operation}.json", evidence)
        print(json.dumps({key: evidence[key] for key in ("mode", "session_id", "version_id", "digest", "had_checkpoint")}))


if __name__ == "__main__":
    if os.name != "posix" or not Path("/proc").exists():
        raise SystemExit("Run only on the assigned Linux cloud VM")
    main()
