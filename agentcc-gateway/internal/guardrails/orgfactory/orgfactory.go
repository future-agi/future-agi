// Package orgfactory builds a guardrail from an organization's pushed config.
//
// It is the one factory the gateway wires for org config (the dynamic factory
// in cmd/agentcc), so every org-config guardrail is built here and nowhere
// else. That single seam is what lets the gateway treat an org config as
// caller-controlled. Every guardrail here is built with its package's New,
// the default constructor, which never reads the environment: futureagi.New
// and external.New take every credential and URL literally and refuse an
// environment reference, so an org config can resolve nothing from the
// gateway's own environment. Every check that calls a URL the
// org config names (futureagi, webhook, external) dials through the guarded
// egress dialer the factory is built with. Only a config.yaml rule
// (cmd/agentcc's registry loop) is operator-trusted and built with
// NewConfigFileRule, which expands ${VAR} and keeps a plain client; nothing
// here calls it.
package orgfactory

import (
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/contentmod"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/hallucination"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/injection"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/language"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/mcpsec"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/sysprompt"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/toolperm"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/topic"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// New returns the guardrails.DynamicFactory the guardrail plugin calls to
// build an org's checks: it builds the guardrail an organization's config
// names, or nil when the name and config match no known guardrail. dialer is
// the guarded egress dialer (cmd/agentcc builds it from
// mcp.egress.private_allow) every org futureagi, webhook and external check
// dials through; a nil dialer is the guard with no exceptions.
func New(dialer *egress.Dialer) guardrails.DynamicFactory {
	return func(name string, cfg map[string]interface{}) guardrails.Guardrail {
		return build(name, cfg, dialer)
	}
}

// build is New's factory for one check.
func build(name string, cfg map[string]interface{}, dialer *egress.Dialer) guardrails.Guardrail {
	switch name {
	case "pii-detection":
		return pii.New(cfg)
	case "content-moderation":
		return contentmod.New(cfg)
	case "keyword-blocklist":
		return blocklist.New(cfg)
	case "input-validation":
		return validation.New(cfg)
	case "prompt-injection":
		return injection.New(cfg)
	case "secret-detection":
		return secrets.New(cfg)
	case "topic-restriction":
		return topic.New(cfg)
	case "language-detection":
		return language.New(cfg)
	case "system-prompt-protection":
		return sysprompt.New(cfg)
	case "hallucination-detection":
		return hallucination.New(cfg)
	case "data-leakage-prevention":
		return leakage.New(cfg)
	}
	if futureagi.IsFutureAGIConfig(cfg) {
		return futureagi.New(name, cfg, dialer)
	}
	if webhook.IsWebhookConfig(cfg) {
		return webhook.New(name, cfg, dialer)
	}
	if expression.IsExpressionConfig(cfg) {
		return expression.New(name, cfg)
	}
	if toolperm.IsToolPermConfig(cfg) {
		return toolperm.New(name, cfg)
	}
	if mcpsec.IsMCPSecConfig(cfg) {
		return mcpsec.New(name, cfg)
	}
	if external.IsExternalProviderConfig(cfg) {
		return external.New(name, cfg, dialer)
	}
	return nil
}
