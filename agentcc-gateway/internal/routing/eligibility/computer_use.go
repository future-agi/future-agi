package eligibility

import (
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const CodeComputerUseUnavailable = "computer_use_unavailable"

func checkComputerUse(c *checker) (code, reason string) {
	in := &c.in
	if !in.ComputerUse.Required {
		return "", ""
	}
	mode := models.CredentialBYOK
	if in.Managed {
		mode = models.CredentialManaged
	}
	canonical := in.ACLModel
	if canonical == "" {
		canonical = in.Model
	}
	supported := func(endpoint *catalog.Endpoint) bool {
		for _, b := range in.ComputerUseBindings {
			if b.CanonicalModel != canonical || b.ProviderID != in.ProviderID || b.SentModel != in.Model || b.CredentialType != mode || !modeldb.MatchesComputerUse(b, in.ComputerUse) {
				continue
			}
			if endpoint != nil && (b.EndpointID != endpoint.ID || b.UpstreamProvider != endpoint.UpstreamProvider) {
				continue
			}
			if in.ComputerUseSource.Lookup(b).Support() == modeldb.ComputerUseSupported {
				return true
			}
		}
		return false
	}
	if c.hasEndpoints {
		if c.narrow(supported) {
			return "", ""
		}
	} else if supported(nil) {
		return "", ""
	}
	return CodeComputerUseUnavailable, "computer use is unavailable for this route"
}
