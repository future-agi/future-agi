package eligibility

import "github.com/futureagi/agentcc-gateway/internal/routing/reqopts"

// The provider-object fields this engine enforces (rules 5 to 8), so a
// request that sends them is no longer refused as not yet supported
// (decision 11).
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
