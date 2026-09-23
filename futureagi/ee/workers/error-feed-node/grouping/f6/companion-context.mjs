import {digest} from './common.mjs';

// Only ordinary accepted input findings are eligible; never scan arbitrary raw
// controller prose or benchmark annotations for extra claims. A shared event is
// a retrieval cue, not proof of a shared issue or a causal relationship.
export function companionsFor(row, rows) {
  if(row.control)return [];
  const events=new Set(row.source_event_ids||[]);
  return rows.filter(other=>other.id!==row.id&&!other.control
    && other.organization_id===row.organization_id&&other.project_id===row.project_id
    && other.workspace_id===row.workspace_id&&other.trace_id===row.trace_id
    && other.engine_version===row.engine_version&&other.scan_version===row.scan_version
    && other.investigation_report_ref?.report_id===row.investigation_report_ref?.report_id)
    .map(other=>({row:other,overlap:[...new Set(other.source_event_ids||[])].filter(id=>events.has(id))}))
    .filter(item=>item.overlap.length)
    .sort((a,b)=>b.overlap.length-a.overlap.length||a.row.id.localeCompare(b.row.id));
}

export function addCompanionEvidence(rows,policy) {
  if(!policy.companion_context)return rows;
  return rows.map(row=>{
    const selected=companionsFor(row,rows).slice(0,policy.companion_limit);
    if(!selected.length)return row;
    const additions=selected.map(({row:other,overlap})=>({
      id:`companion:${other.id}`,text:other.summary,digest:digest(other.summary),
      provenance:'accepted_companion_finding_report',
      reference:{occurrence_id:other.id,finding_id:other.upstream_finding_id,trace_id:other.trace_id,
        kind:other.kind,evidence_revision:other.evidence_revision,source_event_ids:other.source_event_ids,
        shared_event_ids:overlap,link_status:'candidate_only_not_established'},
    }));
    return {...row,evidence:[...row.evidence,...additions],
      source_digest:digest([row.source_digest,additions]),
      evidence_revision:digest([row.evidence_revision,additions])};
  });
}

export const companionInstructions=`Companion evidence is an accepted finding report from the SAME investigation, not a raw event and not automatic proof of a shared mechanism. Shared trace/event IDs are retrieval cues only.
An outcome finding may join a behavioral issue when its OWN report plus an explicitly cited companion report establish the same operation, target and failure. State the concrete linkage in the mechanism explanation; cite the focal finding's original report and the linking companion. Do not reject solely because kind is outcome or because a behavioral explanation is in a companion report. If the relationship is not supported, defer.
Differentiate direct consequences of a faulty action from a separate downstream recovery or false-success mistake. Do not transfer causes between unrelated operations, assume temporal co-occurrence is causal, or treat an upstream report as verified raw execution. Each member and target prototype still needs a citation to its own report. All prior scope, cannot-link and contradictory-evidence rules remain mandatory.`;
