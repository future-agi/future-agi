package outcomes

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
)

// attributePrevOutcome applies x-agentcc-prev-outcome to the subject's
// previous request of the same class: verify_failed, empty and retried
// count against that generation's model (once per generation) and each
// emits one outcome event naming that generation. When the previous request
// left no Last record (it failed, which was already counted, or was served
// from cache, refused, or streamed unclassified), the verdict is dropped,
// never moved to an older generation. ok reports nothing new (the call was
// already counted as answered) and is not attributed. It runs before the
// current request becomes the subject's previous one.
func (p *Plugin) attributePrevOutcome(ctx context.Context, rc *models.RequestContext, org, subject string, class affinity.Class, set settings) {
	verdict := rc.Agent.PrevOutcome
	switch verdict {
	case outcomes.VerdictVerifyFailed, outcomes.VerdictEmpty, outcomes.VerdictRetried:
	default:
		return
	}
	prevID, ok := p.deps.Outcomes.Prev(ctx, org, subject, class)
	if !ok || prevID == rc.RequestID {
		return
	}
	last, ok := p.deps.Outcomes.Last(ctx, org, prevID)
	if !ok {
		return
	}
	if set.countOutcomes {
		p.deps.Outcomes.Judge(ctx, org, prevID, last, verdict, set.halfLife)
	}
	p.emit(outcomes.NewEvent(org, prevID, verdict, outcomes.SourcePrevOutcome, nil, last, p.deps.Outcomes.Now()))
}

func (p *Plugin) emit(ev models.OutcomeEvent) {
	if p.deps.Events == nil {
		return
	}
	if sink := p.deps.Events(); sink != nil {
		sink(models.EventKindOutcome, ev)
	}
}
