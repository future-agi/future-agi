"""Run one actual Deep Agents graph against the standalone skills-fakes model.

Unlike the installer, this application also has exporter credentials. It never
passes those to the registry client or includes them in an evidence record.
"""
import json
import os
import sys
from uuid import UUID

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from fi.context import ContextClient
from fi.context.adapters.deepagents import MEMORY_SOURCE, SKILLS_SOURCE, mount_context
from fi.platform import Client, ContextAuth
from fi.telemetry import agent_scope
from fi_instrumentation.fi_types import ProjectType
from fi_instrumentation.otel import HTTPSpanExporter, register
from langchain_openai import ChatOpenAI


def main():
    data = json.load(sys.stdin)
    os.environ.update(FI_API_KEY=data['apiKey'], FI_SECRET_KEY=data['secretKey'],
                      FI_BASE_URL=data['api'], FI_SKILLS_TRACING='on')
    provider = register(project_name='install-turn', project_type=ProjectType.OBSERVE,
                        skills_processor=True, verbose=False,
                        span_exporter=HTTPSpanExporter(endpoint=data['collector'] + '/v1/traces',
                            headers={'X-Api-Key': data['apiKey'], 'X-Secret-Key': data['secretKey']}))
    try:
        with Client(auth=ContextAuth(data['credential']), base_url=data['api'], workspace_id=data['workspace'],
                    default_headers={'X-Organization-Id': data['org']}) as client:
            registry = ContextClient(client)
            snapshot = registry.resolve(data['context'], version=1)
            verified = snapshot.read_all(trace=False)
            assert verified.identity.digest == data['digest']
            model = ChatOpenAI(base_url=data['modelUrl'], api_key=data['fixture'], model='gpt-4o-mini',
                               use_responses_api=False, streaming=False, max_retries=0, timeout=30)
            graph = create_deep_agent(model=model, memory=[MEMORY_SOURCE], skills=[SKILLS_SOURCE],
                backend=lambda runtime: mount_context(verified, default=StateBackend(runtime), authorize=registry.check_access))
            with agent_scope(agent='install-turn', context=snapshot):
                with provider.get_tracer('install-turn').start_as_current_span('Install test turn') as root:
                    trace_id = str(UUID(int=root.get_span_context().trace_id))
                    turn = graph.invoke({'messages': [{'role': 'user', 'content': json.dumps({'request': data['request']})}]})
                    assert data['marker'] in turn['messages'][-1].content
        assert provider.force_flush(timeout_millis=20000)
    finally:
        provider.shutdown()
    print(json.dumps({'traceId': trace_id}))


if __name__ == '__main__':
    main()
