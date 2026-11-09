package server

import (
	"context"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Mixed billing chains (MIXED-HOP-GUARD-1). A request is billed in one mode:
// Future AGI credits (managed) or the org's own key (byok). Every hop source
// can move it to another model or provider after an attempt fails: auto's
// runners-up, the body's models[] and Messages fallbacks[], the org's and the
// gateway's model fallback chains, provider failover and retry. Unless the
// node turns managed.mixed_billing_chains on, no hop may change the
// request's billing mode: a managed request is never served on the org's own
// key, nor a byok request on Future AGI credits.
//
// One check enforces it, mixedBillingRefusal, against the mode the request
// started in (rc.Billing.ChainMode). startAttempt runs it for every attempt,
// whatever its source, so a cross-mode attempt is never sent. Hop admission
// runs it first too (admitModelFallback for model chains, failoverHops.next
// and admitHop for provider failover), before the hop's eligibility verdict
// and credit hold, so a refused hop is never priced or held either.
//
// A refused hop is not an attempt. It goes to the request's skipped-hop
// record (recordCrossModeSkip: a count and the first models.MaxSkippedHops
// entries, each naming the hop and the billing mode it would have run in),
// never to its attempts, so however many candidates a chain refuses, none
// takes a slot of the request's attempt record (models.MaxAttempts). The
// attempt that serves always has one (hasAttemptRoom), so the cost plugin
// and the credit settlement, which read only attempts, bill it. The chain
// goes on to its next candidate; when none is left and the chain refused
// one, the request answers with the error of the last attempt it made
// (chainError). Managed-to-managed and byok-to-byok chains run as before.
//
// The traffic mirror sends a copy of a served request outside the attempt
// path; mirror_guard.go holds it to the same rule and never lets it reach a
// managed provider.

// mixedBillingRefusal returns why an attempt billed as credType may not run
// (models.ReasonPolicy), or "" when it may: always with mixed billing chains
// on, and otherwise when credType is the request's chain mode or the request
// has none yet.
func (h *Handlers) mixedBillingRefusal(rc *models.RequestContext, credType models.CredentialType) string {
	if h.mixedBillingChains {
		return ""
	}
	if mode := rc.Billing.ChainMode; mode != "" && mode != credType {
		return models.ReasonPolicy
	}
	return ""
}

// setChainMode makes credType the request's chain mode unless it has one.
func setChainMode(rc *models.RequestContext, credType models.CredentialType) {
	if rc.Billing.ChainMode == "" {
		rc.Billing.ChainMode = credType
	}
}

// restoreChainBillingMode puts the request's billing mode back to its chain
// mode after a refused hop had resolved to the other one, so the request is
// settled in the mode its attempts were made in.
func restoreChainBillingMode(rc *models.RequestContext) {
	if mode := rc.Billing.ChainMode; mode != "" {
		rc.Metadata["billing_mode"] = string(mode)
	}
}

// hopRouting is the routing state resolving a fallback hop writes before the
// hop is admitted: what a refused hop's resolution is undone to.
type hopRouting struct {
	provider, model             string
	billingMode, managedModel   string
	hasBilling, hasManagedModel bool
}

func saveHopRouting(rc *models.RequestContext) hopRouting {
	s := hopRouting{provider: rc.Provider}
	if rc.Request != nil {
		s.model = rc.Request.Model
	}
	s.billingMode, s.hasBilling = rc.Metadata["billing_mode"]
	s.managedModel, s.hasManagedModel = rc.Metadata["managed_model"]
	return s
}

func (s hopRouting) restore(rc *models.RequestContext) {
	rc.Provider = s.provider
	if rc.Request != nil {
		rc.Request.Model = s.model
	}
	restoreMetadata(rc, "billing_mode", s.billingMode, s.hasBilling)
	restoreMetadata(rc, "managed_model", s.managedModel, s.hasManagedModel)
}

func restoreMetadata(rc *models.RequestContext, key, value string, present bool) {
	if present {
		rc.Metadata[key] = value
	} else {
		delete(rc.Metadata, key)
	}
}

// admitModelFallback resolves fbModel, the next model of the request's model
// fallback chain (auto's runners-up, models[], the org's or the gateway's
// chain), and admits the hop (call-order step 14). A hop that would change
// the request's billing mode is refused before its verdict and hold: it goes
// to the request's skipped-hop record (recordCrossModeSkip) and its
// resolution is undone. So is a hop the request's attempt record has no
// slot for (hasAttemptRoom), which is only logged. ok is false for a hop the
// chain must skip.
func (h *Handlers) admitModelFallback(ctx context.Context, orgID string, orgCfg *tenant.OrgConfig, rc *models.RequestContext, fbModel string) (providers.Provider, string, string, bool) {
	before := saveHopRouting(rc)
	// A hop resolved before any attempt started (a failover that sent
	// nothing) is held to the mode the request was routed in.
	setChainMode(rc, credentialType(rc))
	p, providerID, model, ok := h.resolveFallbackProvider(ctx, orgID, orgCfg, rc, fbModel)
	if !ok {
		return nil, "", model, false
	}
	if reason := h.mixedBillingRefusal(rc, credentialType(rc)); reason != "" {
		h.recordCrossModeSkip(rc, fallbackKind(rc), reason, providerID, model)
		before.restore(rc)
		return nil, "", model, false
	}
	if !h.hasAttemptRoom(rc, fallbackKind(rc), providerID, model) {
		before.restore(rc)
		return nil, "", model, false
	}
	if !h.admitFallbackHop(rc, orgCfg, providerID, model) {
		return nil, "", model, false
	}
	return p, providerID, model, true
}

// recordCrossModeSkip records a hop refused for changing the request's
// billing mode in the request's skipped-hop record, never as an attempt (see
// above), and logs it, value-free, while the record keeps it in full. The
// hop's billing mode is the one it would have run in: the request's routing
// still holds the hop's resolution when it is recorded.
func (h *Handlers) recordCrossModeSkip(rc *models.RequestContext, kind models.AttemptKind, reason, providerID, model string) {
	credType := h.attemptCredential(orgCredentials(rc), providerID, credentialType(rc))
	n := rc.AddSkippedHop(models.SkippedHop{
		Kind:           kind,
		Reason:         reason,
		Provider:       providerID,
		Model:          model,
		CredentialType: credType,
	})
	if n <= models.MaxSkippedHops {
		slog.Info("hop skipped: it would change the request's billing mode",
			"request_id", rc.RequestID, "kind", string(kind), "reason", reason,
			"provider", providerID, "model", model, "credential_type", string(credType),
			"chain_mode", string(rc.Billing.ChainMode), "skipped_hops", n)
	}
}

// chainError is what a model fallback chain that served nothing answers
// with: first, the error that started it (the primary's, or the provider
// failover's), as before the guard; but when the chain refused a cross-mode
// candidate (skipsBefore is the request's skipped-hop count when the chain
// began), the error of the last attempt it made, last. A refused candidate is
// never sent, so the last same-mode attempt's error is the request's.
func chainError(rc *models.RequestContext, skipsBefore int, first, last error) error {
	if rc.SkippedHopCount() > skipsBefore {
		return last
	}
	return first
}
