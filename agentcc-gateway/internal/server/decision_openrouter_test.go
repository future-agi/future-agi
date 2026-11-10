package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func openRouterGateway(t *testing.T) *decisionGateway {
	t.Helper()
	return newDecisionGateway(t, decisionGatewayOptions{includeBodies: true, config: func(cfg *config.Config, upstreamURL string) {
		cfg.Providers["openrouter"] = config.ProviderConfig{
			APIFormat: "systemone", BaseURL: upstreamURL, APIKey: fagiHeldKey,
			Models: []string{"typesafe/jev-1.14", "~typesafe/jev-latest"},
		}
		cfg.Routing.Mirror = config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{
			{SourceModel: "*", TargetProvider: "mirror-sink", TargetModel: "mirror-model", SampleRate: 1},
		}}
	}})
}

func TestDecisionOpenRouterInternalBYOKPreservesModelNamespace(t *testing.T) {
	g := openRouterGateway(t)
	const answer = `{"model":"typesafe/jev-1.14-20261015","answers":{"q":{"type":"refusal"}},"usage":{"input_tokens":2,"output_tokens":1}}`
	g.upstream.setReply(func(w http.ResponseWriter, _ *http.Request) {
		io.WriteString(w, answer)
	})
	for _, routerModel := range []string{"typesafe/jev-1.14", "~typesafe/jev-latest", "openai/gpt-6-luna-decisions"} {
		body := fmt.Sprintf(`{"model":"openrouter/%s","state":"private-state","questions":{"q":{"type":"noul","instructions":"private-instructions"}},"provider":{"allow_fallbacks":false,"require_parameters":false,"only":["TypeSafe"]}}`, routerModel)
		rr := g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey)
		if rr.Code != http.StatusOK || rr.Body.String() != answer {
			t.Fatalf("%s: status %d", routerModel, rr.Code)
		}
		if path, _ := g.upstream.last(); path != "POST /api/alpha/decisions" {
			t.Errorf("upstream path = %q", path)
		}
		if got := g.upstream.sentModel(t); got != routerModel {
			t.Errorf("upstream model = %q, want %q", got, routerModel)
		}
		_, sentBody := g.upstream.last()
		var sent struct {
			Provider struct {
				AllowFallbacks    bool     `json:"allow_fallbacks"`
				RequireParameters bool     `json:"require_parameters"`
				Only              []string `json:"only"`
			} `json:"provider"`
		}
		if err := json.Unmarshal([]byte(sentBody), &sent); err != nil {
			t.Fatal(err)
		}
		if !sent.Provider.AllowFallbacks || !sent.Provider.RequireParameters || len(sent.Provider.Only) != 1 || sent.Provider.Only[0] != "TypeSafe" {
			t.Fatal("routing policy or provider restriction changed")
		}
		if got := g.upstream.lastHeaders().Get(models.ProviderKeyHeader); got != "" {
			t.Fatal("internal provider-key header escaped to the vendor")
		}
	}
	expectOnlyKey(t, g, callerOwnKey, 3)
	if g.mirror.calls() != 0 {
		t.Fatal("decision was mirrored")
	}
	for where, text := range map[string]string{
		"pipeline metadata": g.observer.dump(),
		"request logs":      g.flushLogs(),
		"cached key hashes": fmt.Sprint(keysOf(g.srv.handlers)),
	} {
		if strings.Contains(text, callerOwnKey) {
			t.Errorf("caller key leaked to %s", where)
		}
	}
}

func TestDecisionOpenRouterRefusesMissingAndUntrustedProviderKeys(t *testing.T) {
	g := openRouterGateway(t)
	const body = `{"model":"openrouter/typesafe/jev-1.14","state":"text","questions":{"q":{"type":"noul"}}}`
	expectError(t, g.post(t, keyInternal, body), http.StatusForbidden, models.CodeBYOKRequired)
	expectError(t, g.post(t, keyByok, body, models.ProviderKeyHeader, callerOwnKey), http.StatusBadRequest, models.CodeProviderKeyHeaderNotAllowed)
	// An unrelated org cannot fall through to the operator's OpenRouter key.
	rr := g.post(t, keyByok, body)
	expectError(t, rr, http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
	if !strings.Contains(errorOf(t, rr).Message, "OpenRouter") {
		t.Error("missing-key guidance names the model author rather than its connection")
	}
	expectNoCalls(t, g)
}

func TestDecisionOpenRouterRejectsOpenAIEnvelopeWithoutTranslation(t *testing.T) {
	g := openRouterGateway(t)
	const body = `{"model":"openrouter/openai/gpt-6-luna-decisions","input":"text","questions":[{"type":"predicate","name":"q","question":"valid?"}]}`
	expectError(t, g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey), http.StatusUnprocessableEntity, models.CodeFormatMismatch)
	expectNoCalls(t, g)
}
