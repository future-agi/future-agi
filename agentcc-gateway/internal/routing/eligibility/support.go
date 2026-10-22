package eligibility

import "github.com/futureagi/agentcc-gateway/internal/routing/reqopts"

// The provider-object fields this engine enforces (rules 5 to 8), which a
// request may send.
func init() {
	for _, f := range []string{
		reqopts.FieldProviderOnly,
		reqopts.FieldProviderIgnore,
		reqopts.FieldProviderMaxPrice,
		reqopts.FieldProviderZDR,
		reqopts.FieldProviderDataCollection,
		reqopts.FieldProviderQuantizations,
		reqopts.FieldProviderRequireParameters,
	} {
		reqopts.MarkSupported(f)
	}
}
