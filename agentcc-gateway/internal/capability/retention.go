package capability

import "github.com/futureagi/agentcc-gateway/internal/models"

var retentionRank = map[models.ContentMode]int{
	models.ContentModeFull:     0,
	models.ContentModeMetadata: 1,
	models.ContentModeNone:     2,
}

// RetentionFor is how much of a capability call's content the gateway
// keeps: the most restrictive of the request's resolved content mode
// (rc.Privacy; unresolved reads as full), the card's results_retention and,
// for a call made through Do, the org's tools.policy.retention_override,
// which only tightens. A retention value that is not full, metadata or none
// reads as none: unknown terms keep nothing. A nil card leaves the content
// mode alone.
func RetentionFor(rc *models.RequestContext, card *Card) models.ContentMode {
	mode := models.ContentModeFull
	if rc != nil {
		mode = tighter(mode, rc.Privacy.ContentMode)
		if call := callOf(rc); call != nil && call.retentionOverride != "" {
			mode = tighter(mode, models.ContentMode(call.retentionOverride))
		}
	}
	if card != nil {
		mode = tighter(mode, models.ContentMode(card.ResultsRetention))
	}
	return mode
}

// tighter is the more restrictive of a and b. An empty b changes nothing; an
// unknown one is none.
func tighter(a, b models.ContentMode) models.ContentMode {
	if b == "" {
		return a
	}
	rb, ok := retentionRank[b]
	if !ok {
		return models.ContentModeNone
	}
	if rb > retentionRank[a] {
		return b
	}
	return a
}
