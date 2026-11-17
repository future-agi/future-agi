package eligibility

import "github.com/futureagi/agentcc-gateway/internal/catalog"

// checkBillability keeps only endpoints whose cards can reserve and settle
// the attempt. It runs after policy filters, so a price never grants access
// to an endpoint the org or caller excludes. BYOK is not narrowed.
func checkBillability(c *checker) (string, string) {
	if !c.in.Managed || c.in.Entry == nil || len(c.in.Entry.Endpoints) == 0 {
		return "", ""
	}
	inclusive := c.in.Entry.InclusivePrice(catalog.SourceOpenRouter) != nil
	if !c.narrow(func(e *catalog.Endpoint) bool {
		return e.ManagedBillable(c.in.Model) || (inclusive && e.Source == catalog.SourceOpenRouter && !e.DynamicPrice && !e.Unpriced)
	}) {
		return CodeManagedModelNotAllowed, "model " + c.in.Model + " has no eligible endpoint with supported pricing"
	}
	return "", ""
}
