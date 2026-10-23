package server

import (
	"fmt"
	"strings"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
)

// Request metadata keys holding the presented key's ACL, written by
// peekKeyOrgID before routing in the form the auth plugin writes
// auth_allowed_providers (plugins/auth/auth.go). Callers cannot set them:
// caller metadata refuses the auth_ prefix (isBlockedMetadataKey).
const (
	metaKeyAllowedProviders = "auth_allowed_providers"
	metaKeyAllowedModels    = "auth_allowed_models"
)

// recordKeyACL copies the key's allowed providers and models into rc as
// comma lists, so the provider-ACL exits and eligibility see them before
// the provider is called. A key with no restriction writes nothing.
func recordKeyACL(rc *models.RequestContext, key authpkg.KeyPeek) {
	if len(key.AllowedProviders) > 0 {
		rc.Metadata[metaKeyAllowedProviders] = strings.Join(key.AllowedProviders, ",")
	}
	if len(key.AllowedModels) > 0 {
		rc.Metadata[metaKeyAllowedModels] = strings.Join(key.AllowedModels, ",")
	}
}

// keyProviderACL is the per-key provider ACL of an endpoint that routes to
// a provider: once the provider is resolved and before it is
// called, a key whose allowed_providers does not name rc.Provider is
// refused with 403. A restricted key is refused when no provider was
// recorded at all: every resolution records one, and a path that
// stops doing so must not let the key through. Internal service keys are
// exempt, as on chat (resolveRoute).
func (h *Handlers) keyProviderACL(rc *models.RequestContext) *models.APIError {
	if rc.Metadata["key_type"] == "internal" {
		return nil
	}
	allowed, ok := rc.Metadata[metaKeyAllowedProviders]
	switch {
	case !ok:
		return nil
	case rc.Provider == "":
		return models.ErrForbidden("API key does not have access to an unresolved provider")
	case eligibility.CSVContains(allowed, rc.Provider):
		return nil
	}
	return errKeyProviderDenied(rc.Provider)
}

// keyAccessGroups is chat's first routing step (resolveRoute step 1) for an
// API that names its model but is not routed (the Assistants API): on a
// gateway with access groups, a key that has some has an alias its groups
// define resolved (rc.Model becomes the model it stands for, which is the
// model sent), and is refused with chat's answer when its groups do not
// hold the model, before the provider is resolved. A request that names no
// model passes.
func (h *Handlers) keyAccessGroups(rc *models.RequestContext) *models.APIError {
	groups := splitCSV(rc.Metadata["key_access_groups"])
	if rc.Model == "" || !h.accessGroupChecker.IsEnabled() || len(groups) == 0 {
		return nil
	}
	if resolved := h.accessGroupChecker.ResolveAlias(rc.Model, groups); resolved != rc.Model {
		rc.SetMetadata("model_alias", rc.Model)
		rc.Model = resolved
	}
	matched, allowed := h.accessGroupChecker.Check(rc.Model, groups)
	if !allowed {
		return h.errModelNotInAccessGroups(rc.Model, groups)
	}
	if matched != "" {
		rc.SetMetadata("access_group_matched", matched)
	}
	return nil
}

// errModelNotInAccessGroups is the 403 for a key whose access groups do not
// hold model, as chat answers it (resolveRoute step 1).
func (h *Handlers) errModelNotInAccessGroups(model string, groups []string) *models.APIError {
	return models.ErrForbidden(fmt.Sprintf("Model %q is not available for this API key. %s",
		model, h.accessGroupChecker.DescribeAllowed(groups)))
}

// keyModelACL is the per-key model ACL of an API that names its model but is
// not routed (the Assistants API: an assistant's model, a run's model
// override), on rc.Model. A key whose allowed_models, or whose access groups,
// do not name the model is refused before the provider is called with the
// answer chat gives the same key and model (eligibility rule 1 on an
// explicit route, as judge runs it: 403 model_not_permitted,
// metadata.reason key_model_denied). A request that names no model, and a
// key with no model list and no access groups, pass. Internal keys are held
// to their model list, as on chat.
//
// Scope: a run that names no model runs on its assistant's stored model,
// which is checked when the assistant is created or updated through the
// gateway.
func (h *Handlers) keyModelACL(rc *models.RequestContext) *models.APIError {
	if rc.Model == "" {
		return nil
	}
	in := eligibility.Input{
		Model: rc.Model,
		Key:   eligibility.KeyACL{AllowedModels: rc.Metadata[metaKeyAllowedModels]},
	}
	if groups := rc.Metadata["key_access_groups"]; groups != "" && h.accessGroupChecker.IsEnabled() {
		in.Key.Groups, in.Key.GroupCheck = splitCSV(groups), h.accessGroupChecker
	}
	code, reason := eligibility.CheckKeyACL(&in)
	if code == "" {
		return nil
	}
	return (&Verdict{Code: code, Message: reason}).apiError()
}

// errKeyProviderDenied is the 403 for a key that may not use providerID,
// with the message every provider-ACL exit uses.
func errKeyProviderDenied(providerID string) *models.APIError {
	return models.ErrForbidden("API key does not have access to provider: " + providerID)
}
