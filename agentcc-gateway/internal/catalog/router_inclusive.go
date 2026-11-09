package catalog

import (
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

const RouterInclusiveContract = "openrouter-inclusive-r601-router-1"

// Keep the IDs and upstream quotes for BYOK, but never revive a previously
// qualified router card on refresh or restore. The whole fixed-price catalogue
// is not a vendor-enforced candidate contract (ruling 746g).
func qualifyRouters(snap *Snapshot) {
	for id, m := range snap.Models {
		if routerbilling.FamilyMember(id) {
			m.InclusiveEnabled = false
			m.Inclusive = nil
		}
	}
}

func routerCard(id string, p Prices) routerbilling.Candidate {
	rate := func(x float64) string { return strconv.FormatFloat(x, 'f', -1, 64) }
	return routerbilling.Candidate{ID: id, Prompt: rate(p.Prompt), Completion: rate(p.Completion), CacheRead: rate(p.CacheRead), CacheWrite: rate(p.CacheWrite), Reasoning: rate(p.Reasoning), Request: rate(p.Request), Image: rate(p.Image), Search: rate(p.WebSearch)}
}
