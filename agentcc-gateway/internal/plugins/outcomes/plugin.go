// Package outcomes is the post-plugin that turns every response into agent
// signals: it classifies the response (ok, empty or content_filter) and
// stamps the class on the request log, counts the outcome per agent, step
// and model, remembers an agent-tagged or managed generation for later
// verdicts, pins the session
// to the model that answered, attributes x-agentcc-prev-outcome to the
// session's previous request of the same class, and marks a managed model
// degraded in the catalog when its per-model health falls.
package outcomes

import (
	"context"
	"slices"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	// Priority places the plugin after settlement and before logging. It is a
	// sequential post-plugin: it writes rc.Metadata.
	Priority = 905

	// MetadataOutcomeClass is the gateway-owned metadata key the response's
	// class rides on to the request log.
	MetadataOutcomeClass = "outcome_class"

	// SweepInterval is how often degraded models are re-judged.
	SweepInterval = 30 * time.Second
)

// Deps is what the plugin works with. Health, Events and OrgConfig are read
// on every request, never captured, because the wiring installs them after
// the plugin is built.
type Deps struct {
	Affinity  *affinity.Store
	Outcomes  *outcomes.Store
	Catalog   ModelStates                          // nil: no degraded marking
	Health    func() routing.ModelHealthReader     // nil: no degraded marking
	Events    func() models.EventSink              // nil: events are dropped
	OrgConfig func(orgID string) *tenant.OrgConfig // nil: every org has the defaults
}

// Plugin is the outcomes post-plugin.
type Plugin struct {
	deps     Deps
	degrader *degrader
}

// New returns the plugin.
func New(d Deps) *Plugin {
	return &Plugin{deps: d, degrader: newDegrader(d.Catalog, d.Health)}
}

func (p *Plugin) Name() string  { return "outcomes" }
func (p *Plugin) Priority() int { return Priority }

// ProcessRequest does nothing: the plugin only reads responses.
func (p *Plugin) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// RunRecovery re-judges degraded models every SweepInterval until ctx ends.
func (p *Plugin) RunRecovery(ctx context.Context) { p.degrader.run(ctx, SweepInterval) }

// ProcessResponse records what the response says about the model. Requests
// that made no upstream call (refused before routing, cache hits) record
// nothing about a model, and their outcome_class is never the caller's; like
// every agent request, they still report the caller's verdict on the
// session's previous request and become the session's previous request.
func (p *Plugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	scrubCallerClass(rc)
	attempts := rc.AttemptsSnapshot()
	p.degrader.observe(attempts)
	org := orgOf(rc)
	if org == "" || p.deps.Outcomes == nil {
		return pipeline.ResultContinue()
	}
	set := p.settings(org, rc)
	subject := affinity.Subject(rc.Agent)
	class, hasClass := affinity.ClassOf(rc.Agent.Step)
	pinnable := subject != "" && hasClass
	if pinnable {
		p.attributePrevOutcome(ctx, rc, org, subject, class, set)
	}
	served, classified := p.record(ctx, rc, org, attempts, set)
	if !pinnable {
		return pipeline.ResultContinue()
	}
	// This request is now the one the subject's next header reports on,
	// whatever came of it: after a failure, a cache hit, a refusal or an
	// unclassified stream, that header finds no Last record and is dropped,
	// rather than landing on an older generation. Written after Remember, so
	// a concurrent call in the session never reads a pointer to a generation
	// whose record is not there yet.
	p.deps.Outcomes.SetPrev(ctx, org, subject, class, rc.RequestID)
	if classified && set.pin && p.deps.Affinity != nil {
		p.deps.Affinity.Set(ctx, org, subject, class, affinity.Value{Model: served.Model, Provider: served.Provider,
			RequestID: rc.RequestID, PrefixTokens: promptTokens(rc.Response)}, set.pinTTL)
	}
	return pipeline.ResultContinue()
}

// record counts the request's outcome against the model that served it and,
// when the response is classified, stamps its class and, for agent-tagged or
// managed traffic, remembers the generation for later verdicts. It returns
// the served attempt and whether the response was classified, the only kind
// a session is pinned after.
func (p *Plugin) record(ctx context.Context, rc *models.RequestContext, org string, attempts []models.Attempt, set settings) (models.Attempt, bool) {
	if len(attempts) == 0 {
		return models.Attempt{}, false
	}
	final := servedAttempt(attempts)
	key := outcomes.Key{Org: org, Agent: rc.Agent.AgentID, Step: rc.Agent.Step, Model: final.Model}
	if final.Outcome != models.OutcomeSuccess {
		if set.countOutcomes && modelError(final) {
			p.deps.Outcomes.Record(ctx, key, outcomes.Outcome{ErrorClass: final.ErrorClass, LatencyMs: final.LatencyMs}, set.halfLife)
		}
		return final, false
	}
	cls, ok := outcomes.Classify(rc.Response)
	if !ok {
		return final, false
	}
	rc.SetMetadata(MetadataOutcomeClass, string(cls))
	if set.countOutcomes {
		p.deps.Outcomes.Record(ctx, key, cls.Outcome(final.LatencyMs), set.halfLife)
	}
	if remembered(rc, final) {
		p.deps.Outcomes.Remember(ctx, org, rc.RequestID, outcomes.Last{Agent: rc.Agent.AgentID, Run: rc.Agent.RunID,
			Step: rc.Agent.Step, Model: final.Model, Class: cls})
	}
	return final, true
}

// remembered reports whether the gateway keeps the generation's Last record,
// which POST /v1/feedback and x-agentcc-prev-outcome judge: only for
// agent-tagged traffic (the parse hook found an agent identity, as
// applyAgentContext's agent_tagged says) or a managed attempt served it.
// An untagged BYOK request writes no per-request key.
func remembered(rc *models.RequestContext, served models.Attempt) bool {
	return rc.Agent.IdentitySource != "" || served.CredentialType == models.CredentialManaged
}

// servedAttempt is the attempt whose result the caller got: the last that
// succeeded (a hedge's loser comes after its winner), else the last one.
func servedAttempt(attempts []models.Attempt) models.Attempt {
	for i := len(attempts) - 1; i >= 0; i-- {
		if attempts[i].Outcome == models.OutcomeSuccess {
			return attempts[i]
		}
	}
	return attempts[len(attempts)-1]
}

// scrubCallerClass drops an outcome_class the caller sent as metadata, and
// its name from the caller's keys: the key is the gateway's, and the request
// log must never carry a forged one. The plugin runs in the sequential post
// phase, before any parallel reader.
func scrubCallerClass(rc *models.RequestContext) {
	if _, ok := rc.Metadata[MetadataOutcomeClass]; !ok {
		return
	}
	delete(rc.Metadata, MetadataOutcomeClass)
	rc.CustomMetadataKeys = slices.DeleteFunc(rc.CustomMetadataKeys, func(k string) bool { return k == MetadataOutcomeClass })
}

// modelError reports whether a failed attempt says something about the
// model: an upstream failure, not a refusal, a cancellation or the caller.
func modelError(a models.Attempt) bool {
	if a.Outcome != models.OutcomeError && a.Outcome != models.OutcomeTimeout {
		return false
	}
	switch a.ErrorClass {
	case "", models.ErrClassPolicy, models.ErrClassBudget, models.ErrClassInsufficientCredits,
		models.ErrClassCancelled, models.ErrClassCircuitOpen:
		return false
	}
	return a.ErrorOrigin != models.OriginClient
}

func promptTokens(resp *models.ChatCompletionResponse) int {
	if resp == nil || resp.Usage == nil {
		return 0
	}
	return resp.Usage.PromptTokens
}

func orgOf(rc *models.RequestContext) string {
	if id := rc.Metadata["org_id"]; id != "" {
		return id
	}
	return rc.Metadata["key_org_id"]
}

// settings are the org's outcome-feedback and affinity settings, from the
// config variant the request ran under.
type settings struct {
	countOutcomes bool
	halfLife      time.Duration
	pin           bool
	pinTTL        time.Duration
}

func (p *Plugin) settings(org string, rc *models.RequestContext) settings {
	var cfg *tenant.OrgConfig
	if p.deps.OrgConfig != nil {
		cfg = p.deps.OrgConfig(org)
	}
	if cfg != nil && rc.Evidence.PolicyVariant == tenant.PolicyVariantCandidate {
		cfg = cfg.Candidate()
	}
	o, a := cfg.AutoOutcomes(), cfg.AutoAffinity()
	return settings{countOutcomes: o.Enabled, halfLife: outcomes.HalfLife(o.WindowSeconds),
		pin: a.Enabled, pinTTL: time.Duration(max(a.TTLSeconds, 1)) * time.Second}
}
