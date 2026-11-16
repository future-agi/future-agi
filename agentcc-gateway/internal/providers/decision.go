package providers

import (
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/providers/systemone"
)

// DecisionDialectOpenAI is the dialect name of OpenAI's Decisions API, the
// model prefix an internal caller uses for it ("openai/gpt-6-luna").
const DecisionDialectOpenAI = "openai"

// defaultOpenAIBaseURL is where OpenAI Decisions go when config.yaml has no
// openai provider.
const defaultOpenAIBaseURL = "https://api.openai.com"

// decisionProxy picks the proxy for a decision call: the operator's
// HTTP_PROXY, HTTPS_PROXY and NO_PROXY, for System One and OpenAI Decisions.
// Chat connections remain direct. A variable so tests can send decision calls
// to a test proxy.
var decisionProxy = http.ProxyFromEnvironment

// DecisionDialectFormat returns the wire format a decision dialect speaks:
// "typesafe", "perplexity" and "openrouter" speak System One; "openai" speaks
// OpenAI Decisions.
func DecisionDialectFormat(dialect string) (string, bool) {
	if strings.EqualFold(dialect, DecisionDialectOpenAI) {
		return models.DecisionFormatOpenAI, true
	}
	if _, ok := systemone.DialectByName(dialect); ok {
		return models.DecisionFormatSystemOne, true
	}
	return "", false
}

// DecisionDialectBaseURLs returns, for each decision dialect, the base_url
// of the first config.yaml provider (in ID order) that speaks it. Only that
// base_url is ever taken from it: a call made with a caller's own key never
// borrows the provider's api_key, headers or path prefix.
func DecisionDialectBaseURLs(cfgs map[string]config.ProviderConfig) map[string]string {
	ids := make([]string, 0, len(cfgs))
	for id := range cfgs {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	bases := make(map[string]string)
	for _, id := range ids {
		cfg := cfgs[id]
		base := strings.TrimSpace(cfg.BaseURL)
		if base == "" {
			continue
		}
		var dialect string
		switch {
		case IsSystemOneFormat(cfg.APIFormat):
			d, err := systemone.DialectFor(id, base)
			if err != nil || d.Name == systemone.Generic.Name {
				continue
			}
			dialect = d.Name
		case cfg.APIFormat == "openai" && id == DecisionDialectOpenAI:
			dialect = DecisionDialectOpenAI
		default:
			continue
		}
		if _, taken := bases[dialect]; !taken {
			bases[dialect] = base
		}
	}
	return bases
}

// NewKeyedDecisionProvider builds a decision provider for one caller-supplied
// key: dialect picks the vendor, baseURL overrides its default upstream.
func NewKeyedDecisionProvider(dialect, baseURL, apiKey string) (Provider, error) {
	cfg := config.ProviderConfig{
		BaseURL:        baseURL,
		APIKey:         apiKey,
		DefaultTimeout: 60 * time.Second,
		MaxConcurrent:  100,
		ConnPoolSize:   100,
		Proxy:          decisionProxy,
	}
	if strings.EqualFold(dialect, DecisionDialectOpenAI) {
		if cfg.BaseURL == "" {
			cfg.BaseURL = defaultOpenAIBaseURL
		}
		cfg.APIFormat = "openai"
		return openai.New(DecisionDialectOpenAI, cfg)
	}
	d, ok := systemone.DialectByName(dialect)
	if !ok {
		return nil, fmt.Errorf("unknown decision dialect %q", dialect)
	}
	cfg.APIFormat = APIFormatSystemOne
	return systemone.NewForDialect(d.Name, cfg, d)
}

// IsOpenRouterChat reports whether a provider saved with the OpenAI format is
// an OpenRouter connection: its base_url's host, or else its ID, names
// OpenRouter. Such a provider serves chat on its OpenAI-compatible API and
// System One decisions on OpenRouter's decisions endpoint, on the same key.
// TypeSafe or Perplexity saved with the OpenAI format is not one.
func IsOpenRouterChat(apiFormat, providerID, baseURL string) bool {
	if apiFormat != "openai" {
		return false
	}
	d, err := systemone.DialectFor(providerID, baseURL)
	return err == nil && d.Name == systemone.OpenRouter.Name
}

// NewOpenRouterDecisionProvider builds the System One provider an OpenAI-format
// OpenRouter connection serves decisions with. It keeps the connection's key,
// timeouts and dialer, and posts to the origin of its base_url (scheme, host
// and port: "https://openrouter.ai/api/v1" posts to
// "https://openrouter.ai/api/alpha/decisions"), or to OpenRouter's own when it
// states none. The chat path prefix is never used. A connection with no dialer
// of its own takes the operator's decision proxy, as other decision providers.
func NewOpenRouterDecisionProvider(id string, cfg config.ProviderConfig) (Provider, error) {
	cfg.APIFormat = APIFormatSystemOne
	cfg.BaseURL = baseURLOrigin(cfg.BaseURL)
	cfg.APIPathPrefix = nil
	if cfg.Proxy == nil && cfg.DialContext == nil {
		cfg.Proxy = decisionProxy
	}
	return systemone.NewForDialect(id, cfg, systemone.OpenRouter)
}

// baseURLOrigin returns scheme://host[:port] of a base URL, "" for none. A
// URL that does not parse is returned as is, for the provider to refuse.
func baseURLOrigin(baseURL string) string {
	baseURL = strings.TrimSpace(baseURL)
	if baseURL == "" {
		return ""
	}
	u, err := url.Parse(baseURL)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return baseURL
	}
	return u.Scheme + "://" + u.Host
}
