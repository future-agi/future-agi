package routing

// AutoModelNames are the model names that ask the gateway to choose. The
// first is the canonical one, listed by /v1/models. omega-default is the
// model Omega's gatewayModel() sends, and omega/auto its explicit form; the
// agent context hook resolves both (routing.agents.default_model, then the
// org's model map, then auto).
var AutoModelNames = []string{"auto", "agentcc/auto", OmegaDefaultModel, OmegaAutoModel}

// The Omega aliases.
const (
	OmegaDefaultModel = "omega-default"
	OmegaAutoModel    = "omega/auto"
)

// IsAutoModel reports whether model asks the gateway to choose.
func IsAutoModel(model string) bool {
	for _, n := range AutoModelNames {
		if model == n {
			return true
		}
	}
	return false
}

// IsOmegaAlias reports whether model is one of the Omega aliases, which an
// org can pin to a model of its own.
func IsOmegaAlias(model string) bool {
	return model == OmegaDefaultModel || model == OmegaAutoModel
}
