import { readFileSync } from 'node:fs';
import {
  createOtlpFixture, type OtlpExport, type OtlpScope, type OtlpSpan,
} from '../lib/otlp';

/** SYNTHETIC fixtures, never evidence of a live vendor session. Source at the
 * candidate head: fi-collector/exporter/clickhouse25exporter/testdata/
 * copilot_1_0_98_otlp.json. Preserve its int64 usage values and five-span tree.
 * Routing IDs are deliberately removed; the sender's fresh actor keys own scope.
 * No real coding-agent homes, credentials or external providers are consulted. */
const source = JSON.parse(readFileSync(new URL(
  '../../fi-collector/exporter/clickhouse25exporter/testdata/copilot_1_0_98_otlp.json', import.meta.url),
'utf8')) as OtlpExport;

export interface SyntheticCopilotOptions {
  projectName: string;
  /** Lowercase 32-hex wire ID. Mint once, then reuse the frozen fixture for replay. */
  traceId?: string;
  /** Omit for exact source times; supply recent times for retention/UI tests. */
  startTimeUnixNano?: string;
  endTimeUnixNano?: string;
  conversationId?: string;
  scope?: OtlpScope;
}

export function syntheticCopilotFixture(options: SyntheticCopilotOptions): OtlpExport {
  return createOtlpFixture({ resourceSpans: source.resourceSpans.map(resource => ({
    resource: { attributes: resource.resource.attributes
      .filter(a => !['fi.project_id', 'fi.org_id'].includes(a.key))
      .map(a => a.key === 'project_name' ? { key: a.key, value: { stringValue: options.projectName } } : a) },
    scopeSpans: resource.scopeSpans.map(scope => ({
      scope: options.scope ?? scope.scope,
      spans: scope.spans.map(span => ({ ...span,
        traceId: options.traceId ?? span.traceId,
        startTimeUnixNano: options.startTimeUnixNano ?? span.startTimeUnixNano,
        endTimeUnixNano: options.endTimeUnixNano ?? span.endTimeUnixNano,
        attributes: span.attributes?.map(a => a.key === 'gen_ai.conversation.id' && options.conversationId !== undefined
          ? { key: a.key, value: { stringValue: options.conversationId } } : a),
      })),
    })),
  })) });
}

/** Synthetic details use the collector's allowed event/link keys, pinned from
 * fi-collector/pkg/normalization/profiles_data.go and converter's
 * normalization_test.go:TestEventsAndLinksReachStoredRow. No message/body text.
 * Sending these fields proves nothing about storage until separately asserted. */
export function syntheticCopilotDetailsFixture(options: SyntheticCopilotOptions): OtlpExport {
  const fixture = syntheticCopilotFixture(options);
  return createOtlpFixture({ resourceSpans: fixture.resourceSpans.map(resource => ({ ...resource,
    scopeSpans: resource.scopeSpans.map(scope => ({ ...scope,
      spans: scope.spans.map((span, index): OtlpSpan => index !== 0 ? span : ({ ...span,
        status: { code: 2, message: 'synthetic timeout' },
        events: [{ name: 'exception', timeUnixNano: span.startTimeUnixNano,
          attributes: [{ key: 'exception.type', value: { stringValue: 'Timeout' } }],
          droppedAttributesCount: 0 }],
        links: [{ traceId: span.traceId, spanId: scope.spans[1].spanId,
          attributes: [{ key: 'fi.agent.caused_by.work_id', value: { stringValue: 'synthetic-work' } }],
          droppedAttributesCount: 0, flags: 1 }],
      })),
    })),
  })) });
}

/** One known scope plus an unknown-scope control in the first resource, and
 * another resource/project. Shared span IDs across DIFFERENT trace IDs are
 * deliberate: selectors and receipts must key by the pair, not span ID alone. */
export function syntheticMixedFixture(options: SyntheticCopilotOptions & {
  unknownTraceId: string; siblingTraceId: string; siblingProjectName: string;
}): OtlpExport {
  const native = syntheticCopilotFixture(options).resourceSpans[0];
  const unknown = syntheticCopilotFixture({ ...options, traceId: options.unknownTraceId,
    scope: { name: 'unrelated-sdk', version: 'synthetic-unknown' } }).resourceSpans[0].scopeSpans[0];
  const sibling = syntheticCopilotFixture({ ...options, traceId: options.siblingTraceId,
    projectName: options.siblingProjectName }).resourceSpans[0];
  return createOtlpFixture({ resourceSpans: [
    { ...native, scopeSpans: [...native.scopeSpans, unknown] }, sibling,
  ] });
}
