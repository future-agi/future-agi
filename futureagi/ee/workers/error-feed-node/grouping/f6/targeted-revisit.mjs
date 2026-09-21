import assert from 'node:assert/strict';
import {digest, pairSafety, scopeKey} from './common.mjs';
import {discoverySchema, validateDiscoveryParts, evidenceReceipt} from './admission.mjs';

// Only changes the model could observe count as material novelty. Routine
// membership growth does not qualify until it changes a displayed prototype.
export function targetSignature(issue, byId) {
  return digest({id:issue.id, revision:issue.mechanism_revision,
    mechanism:issue.mechanism, fix_hypothesis:issue.fix_hypothesis, falsifier:issue.falsifier,
    prototypes:issue.prototypes.map(id=>[id,byId.get(id).evidence_revision])});
}
export function eligibleTarget(id, issue, context) {
  return issue.active && issue.scope===scopeKey(context.byId.get(id))
    && !issue.members.some(other=>pairSafety(context.byId.get(id),context.byId.get(other),context.constraints));
}
export function snapshotTargets(id, registry, context) {
  return Object.fromEntries(registry.issues.filter(issue=>eligibleTarget(id,issue,context))
    .map(issue=>[issue.id,targetSignature(issue,context.byId)]));
}
export function rankNovelReviews({findings,state,context,index,retrieveIssues}) {
  const membership=new Set(state.registry.issues.filter(i=>i.active).flatMap(i=>i.members));
  return findings.filter(id=>!membership.has(id)&&!state.targeted_attempts?.[id]&&state.targeted_seen?.[id])
    .flatMap(id=>{
      // Same top-k retrieval as discovery. This experiment does not widen or
      // rerank targets; only the queue of findings with genuinely new targets.
      const candidates=retrieveIssues({seed:id,members:[id]},state.registry,index,context.policy)
        .filter(issue=>eligibleTarget(id,issue,context));
      const previous=state.targeted_seen[id].targets;
      const novel=candidates.filter(issue=>previous[issue.id]!==targetSignature(issue,context.byId));
      if(!novel.length)return [];
      return [{id,candidates,novel_ids:novel.map(i=>i.id),
        priority:Math.max(...novel.map(i=>i.retrieval_score)),
        changes:novel.map(i=>({issue_id:i.id,reason:i.id in previous?'changed_target':'new_target',signature:targetSignature(i,context.byId)}))}];
    }).sort((a,b)=>b.priority-a.priority||b.novel_ids.length-a.novel_ids.length||a.id.localeCompare(b.id));
}

export async function runTargetedReviews({findings,state,context,index,retrieveIssues,packInvestigation,investigate,commit,hold,save}) {
  state.targeted_attempts ||= {};
  state.targeted_skipped ||= {};
  while(Object.keys(state.targeted_attempts).length<context.policy.max_targeted_reviews) {
    const queued=rankNovelReviews({findings,state,context,index,retrieveIssues})
      .find(item=>state.targeted_skipped[item.id]!==digest(item.changes));
    if(!queued)break;
    const {id,candidates,changes}=queued;
    const selection={selected:[id],controls:[],roles:{[id]:['targeted_revisit']},unreviewed:[],missing:[]};
    const packed=packInvestigation(selection,candidates,context.byId,context.constraints,
      {...context.policy,max_input_bytes:context.policy.max_input_bytes-2000});
    const exposedChanges=changes.filter(change=>packed.issues.some(issue=>issue.id===change.issue_id));
    if(!exposedChanges.length){state.targeted_skipped[id]=digest(changes);await save();continue;}
    packed.prompt.instructions+='\nTARGETED LATE REVISIT: A supplied issue appeared or materially changed after this finding was last reviewed. Evaluate only whether the selected finding belongs to one of the supplied existing issues. Attach with target_issue_id only when its own cited evidence and the issue prototypes support the same actionable mechanism. Otherwise defer with the missing or incompatible evidence. Never create a new issue, merge issues, change issue identity, or treat candidate novelty/similarity as proof. All citation, contradiction, scope and cannot-link rules remain mandatory.';
    const investigationId=digest([packed.prompt,context.policy.digest]);
    const before=snapshotTargets(id,state.registry,context);
    const result=await investigate(packed.prompt,discoverySchema,[...packed.shown].map(key=>context.byId.get(key)));
    const primaryReceiptId=investigate.receiptFor?.(result)??null;
    const receipt={id:investigationId,phase:'targeted_revisit',selection:packed.selection,
      shown:[...packed.shown],candidate_issues:packed.issues.map(i=>i.id),novel_targets:exposedChanges,
      proposal:result,status:'complete',primary_receipt_id:primaryReceiptId};
    const validation={...context,allowedMembers:new Set([id]),shown:packed.shown,targets:packed.issues};
    try {
      const parts=validateDiscoveryParts(result,validation);receipt.validation_errors=parts.errors;
      assert.ok(parts.groups.every(g=>g.target_issue_id!==null),'Targeted revisit cannot create an issue');
      if(parts.errors.length)receipt.status='incomplete';
      for(const group of parts.groups){
        const admission=evidenceReceipt(group,validation,investigationId);
        admission.model_provenance={primary_receipt_id:primaryReceiptId,
          group_index:result.groups.indexOf(group),repair_receipt_id:null};
        if(admission.admission.state!=='Emerging'){hold(id,'Unresolved contradiction in targeted revisit',investigationId);continue;}
        const target=state.registry.issues.find(i=>i.id===group.target_issue_id&&i.active);
        commit({type:'attach',issue_id:target.id,member_ids:group.member_ids,
          expected_revisions:{[target.id]:target.mechanism_revision},receipt:admission});
        delete state.deferred[id];
      }
      parts.deferred.forEach(d=>hold(d.finding_id,d.reason,investigationId));
    }catch(error){receipt.status='incomplete';receipt.reason=error.message;hold(id,'Unsupported targeted revisit; no membership change',investigationId);}
    // Persist only completed returned work. An unknown provider completion is
    // still guarded by the existing request ledger, never retried here.
    state.targeted_attempts[id]={receipt:investigationId,novel_targets:exposedChanges};
    state.targeted_seen[id]={receipt:investigationId,targets:before};
    state.receipts.push(receipt);await save();
  }
}
