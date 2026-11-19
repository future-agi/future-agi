import { completion } from '../../lib/skills-r3/programmable-provider.mjs';

const call = (id, name, args) => ({ id, type: 'function', function: { name, arguments: JSON.stringify(args) } });
const json = (body, extra = {}) => ({ kind: 'json', body, ...extra });

/** All values are synthetic. Expected selection is supplied here, never in product task messages. */
export function providerScenarios(skillHandle = 'fixture-refund') {
  const root = call('root-1', 'load_skill', { skill_id: skillHandle });
  const read = (id, offset, maxBytes) => call(id, 'read_skill_file', {
    skill_id: skillHandle, path: 'SKILL.md', offset, max_bytes: maxBytes,
  });
  return {
    tool_turns: [json(completion({ content: null, tool_calls: [root] }, { finishReason: 'tool_calls' })),
      json(completion({ content: '', tool_calls: [call('policy-2', 'read_skill_file', { skill_id: skillHandle, path: 'references/policy.md', offset: 0, max_bytes: 24000 })] }, { finishReason: 'tool_calls' })),
      json(completion({ content: '{"eligible":true,"amount":25}' }))],
    parallel_ids: [json(completion({ content: '', tool_calls: [root, read('read-2', 0, 8)] }, { finishReason: 'tool_calls' }))],
    duplicate_ids: [json(completion({ content: null, tool_calls: [root, read('root-1', 0, 8)] }, { finishReason: 'tool_calls' }))],
    malformed_ids: [json(completion({ content: null, tool_calls: [{ type: 'function', function: { name: 'load_skill', arguments: '{}' } }] }, { finishReason: 'tool_calls' }))],
    utf8_overlap: [json(completion({ content: '', tool_calls: [root] }, { finishReason: 'tool_calls' })),
      json(completion({ content: '', tool_calls: [read('r2', 1, 7), read('r3', 2, 11)] }, { finishReason: 'tool_calls' })),
      json(completion({ content: 'Partial reads are not complete delivery.' }))],
    typed_correct: [json(completion({ content: '{"eligible":true,"amount":25}' }))],
    typed_wrong: [json(completion({ content: '{"eligible":"true","amount":"25"}' }))],
    invalid_judge: [json(completion({ content: '{"results":[{"check_id":"not-requested","verdict":"pass","evidence":"invented"}]}' }))],
    invalid_json: [{ kind: 'raw', body: '{not-json', status: 200 }],
    auth_failure: [json({ error: { code: 'fixture_unauthorized' } }, { status: 401 })],
    rate_limit: [json({ error: { code: 'fixture_rate_limit' } }, { status: 429, retry_after: 1 })],
    server_failure: [json({ error: { code: 'fixture_failure' } }, { status: 500 })],
    lost_response: [{ kind: 'disconnect' }],
    missing_usage: [json(completion({ content: 'fixture' }, { usage: null }))],
    late_completion: [json(completion({ content: 'late fixture' }), { barrier: 'before-response' })],
  };
}
