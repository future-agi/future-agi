package providers

import (
	"fmt"
	"net/http"
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
// "typesafe" and "perplexity" speak System One, "openai" OpenAI Decisions.
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
