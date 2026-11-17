package eligibility

import (
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// CheckKeyACL runs rule 1 (key_acl) alone on in, for an API that names its
// model but is not routed (the Assistants API): a key it refuses there gets
// the code and reason Check gives the same key and model on an explicit
// route.
func CheckKeyACL(in *Input) (code, reason string) {
	c := checker{in: *in}
	return checkKeyACL(&c)
}

// checkKeyACL is rule 1: the key's allowed_models, allowed_providers and
// access groups, judged on the model the candidate would be sent, never the
// literal "auto". The provider refusal carries the message every
// provider-ACL exit uses.
func checkKeyACL(c *checker) (string, string) {
	in := &c.in
	model := in.ACLModel
	if model == "" {
		model = in.Model
	}
	if in.Key.AllowedModels != "" && !CSVContains(in.Key.AllowedModels, model) {
		return CodeKeyModelDenied, "API key does not have access to model: " + model + ". " + keyModelsRestriction(in.Key.AllowedModels)
	}
	if !in.Key.Internal && in.Key.AllowedProviders != "" && !CSVContains(in.Key.AllowedProviders, in.ProviderID) {
		return CodeKeyProviderDenied, "API key does not have access to provider: " + in.ProviderID
	}
	if in.Key.GroupCheck != nil && len(in.Key.Groups) > 0 {
		if _, ok := in.Key.GroupCheck.Check(model, in.Key.Groups); !ok {
			return CodeKeyModelDenied, "model " + model + " is not in this API key's access groups"
		}
	}
	return "", ""
}

// checkOrgProviderLock is rule 2: the org's routing.provider_lock, when
// enabled, on the gateway provider. Deny wins over allow; an empty allow
// list allows every provider the deny list leaves.
func checkOrgProviderLock(c *checker) (string, string) {
	if c.in.Org == nil || c.in.Org.Routing == nil {
		return "", ""
	}
	lock := c.in.Org.Routing.ProviderLock
	if lock == nil || !lock.Enabled {
		return "", ""
	}
	p := c.in.ProviderID
	if anyGlob(lock.DenyProviders, p) || (len(lock.AllowedProviders) > 0 && !anyGlob(lock.AllowedProviders, p)) {
		return CodeOrgProviderDenied, "your organization's provider lock does not allow provider " + p
	}
	return "", ""
}

// checkManaged is rule 3, for a managed candidate: the org must have opted
// in to managed models, and the model must pass managed_models' deny lists,
// then its allow lists (models and vendors, globs; deny wins).
func checkManaged(c *checker) (string, string) {
	if !c.in.Managed {
		return "", ""
	}
	if !c.in.Org.ManagedModelsEnabled() {
		return CodeManagedNotEnabled, "managed models are not enabled for your organization"
	}
	m := c.in.Org.ManagedPolicy()
	model := c.in.Model
	canonical := catalog.Canonical(c.in.ProviderID, model)
	vendor := vendorOf(canonical)
	// Denying a base model denies its listed variants too. Allows remain
	// exact/glob matches on the dispatched ID: a base allow is not a grant
	// to a differently priced or behaving variant.
	base := catalog.VariantBase(model)
	denied := anyGlob(m.DenyModels, model) || anyGlob(m.DenyModels, base) || anyGlob(m.DenyModels, canonical) || (vendor != "" && anyGlob(m.DenyVendors, vendor))
	restricted := len(m.AllowModels) > 0 || len(m.AllowVendors) > 0
	allowed := anyGlob(m.AllowModels, model) || (vendor != "" && anyGlob(m.AllowVendors, vendor))
	freeDenied := !m.AllowFree && c.in.Entry.ExplicitlyFree(catalog.SourceOpenRouter)
	if denied || freeDenied || (restricted && !allowed) {
		return CodeManagedModelNotAllowed, "your organization's managed_models policy does not allow " + model
	}
	return "", ""
}

// checkBillingMode is rule 9: the caller's billing-mode restriction
// (x-agentcc-billing-mode, or a runtime claim) only narrows.
func checkBillingMode(c *checker) (string, string) {
	switch c.in.ModeRestriction {
	case models.BillingModeBYOK:
		if c.in.Managed {
			return CodeBillingModeRestricted, "the request is restricted to your own provider keys (byok)"
		}
	case models.BillingModeManaged:
		if !c.in.Managed {
			return CodeBillingModeRestricted, "the request is restricted to managed models"
		}
	}
	return "", ""
}

// maxNamedAllowedModels is the most allowed models a key-restriction refusal
// names.
const maxNamedAllowedModels = 10

// keyModelsRestriction names the key's restriction to the models in
// allowed, a comma list, for the refusal of a model it leaves out.
func keyModelsRestriction(allowed string) string {
	var names []string
	for _, entry := range strings.Split(allowed, ",") {
		if entry = strings.TrimSpace(entry); entry != "" {
			names = append(names, entry)
		}
	}
	listed := names
	if len(listed) > maxNamedAllowedModels {
		listed = listed[:maxNamedAllowedModels]
	}
	sentence := "This key is restricted to its allowed models (" + strings.Join(listed, ", ")
	if more := len(names) - len(listed); more > 0 {
		sentence += fmt.Sprintf(" and %d more", more)
	}
	return sentence + "); call one of them, or use a key without this restriction."
}
