// One request to the isolated standalone stack. Credentials arrive on stdin, never argv.
import { readFileSync } from 'node:fs';
import { Client } from '@future-agi/sdk/platform';
import { ContextClient } from '@future-agi/sdk/context';
import { SkillsClient } from '@future-agi/sdk/skills';
import { withAgentScope } from '@future-agi/sdk/telemetry';
import { ProjectType, register } from '@traceai/fi-core';

const { credential, apiKey, secretKey, project, agents } = JSON.parse(readFileSync(0, 'utf8'));
const client = new Client({ baseUrl: 'http://app:8000', auth: { type: 'context', credential }, maxRetries: 0 });
const provider = register({ projectName: project, projectType: ProjectType.OBSERVE,
  endpoint: 'http://app:4318/v1/traces', headers: { 'x-api-key': apiKey, 'x-secret-key': secretKey } });
const tracer = provider.getTracer('support-ts');
try {
  const traceId = await tracer.startActiveSpan('TypeScript support request', async span => {
    try {
      for (const agent of agents) {
        await withAgentScope({ agent: agent.name }, async () => {
          if (agent.context) {
            const snapshot = await new ContextClient(client).resolve(agent.context.name, agent.context.selector);
            await snapshot.readAll(); // verifies the closure; not a load of every linked skill
          }
          for (const skill of agent.skills) {
            const snapshot = await new SkillsClient(client).resolve(skill.name, skill.selector);
            await snapshot.readFile('SKILL.md');
          }
          tracer.startSpan('prepare answer').end();
        });
      }
      return span.spanContext().traceId;
    } finally { span.end(); }
  });
  await provider.forceFlush();
  console.log(JSON.stringify({ trace_id: traceId.replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/, '$1-$2-$3-$4-$5') }));
} finally { await provider.shutdown(); }
