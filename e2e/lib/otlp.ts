import { randomBytes, randomUUID } from 'node:crypto';
import type { APIRequestContext } from '@playwright/test';

/** `traceId` is the dashed-UUID form the collector writes to CH `spans.trace_id` /
 *  `traces.trace_id` (converter.go `traceIDToUUIDString`); the OTLP wire carries
 *  its undashed hex. `spanIds` are the 16-char hex CH `spans.id` values. */
export interface SeededTrace { traceId: string; spanIds: string[]; projectName: string }

export interface SendTraceConfig {
  collectorUrl: string; apiKey: string; secretKey: string; projectName: string;
  rootName?: string;
}

const attr = (key: string, value: string) => ({ key, value: { stringValue: value } });
const nsNow = () => (BigInt(Date.now()) * 1_000_000n);

export async function sendTrace(req: APIRequestContext, cfg: SendTraceConfig): Promise<SeededTrace> {
  const traceId = randomUUID();
  const rootId = randomBytes(8).toString('hex');
  const childId = randomBytes(8).toString('hex');
  const start = nsNow();
  const rootName = cfg.rootName ?? 'e2e.root';
  const wireTraceId = traceId.replaceAll('-', '');

  const span = (spanId: string, parentSpanId: string | undefined, name: string,
                extraAttrs: ReturnType<typeof attr>[] = []) => ({
    traceId: wireTraceId, spanId, parentSpanId, name, kind: 1,
    startTimeUnixNano: String(start), endTimeUnixNano: String(start + 50_000_000n),
    attributes: extraAttrs, status: { code: 1 },
  });

  const payload = {
    resourceSpans: [{
      resource: { attributes: [attr('project_name', cfg.projectName), attr('service.name', cfg.projectName)] },
      scopeSpans: [{
        scope: { name: 'e2e-harness' },
        spans: [
          span(rootId, undefined, rootName),
          span(childId, rootId, 'e2e.llm-call', [attr('fi.span.kind', 'llm')]),
        ],
      }],
    }],
  };

  const res = await req.post(`${cfg.collectorUrl}/v1/traces`, {
    headers: { 'X-Api-Key': cfg.apiKey, 'X-Secret-Key': cfg.secretKey, 'Content-Type': 'application/json' },
    data: payload,
  });
  if (res.status() >= 300) throw new Error(`collector ${res.status()}: ${await res.text()}`);
  return { traceId, spanIds: [rootId, childId], projectName: cfg.projectName };
}
