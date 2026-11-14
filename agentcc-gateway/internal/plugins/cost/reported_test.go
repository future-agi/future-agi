package cost

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// usageExtra parses a usage object the way a provider response is parsed,
// so the test sees exactly the Extra a real response produces.
func usageExtra(t *testing.T, usage string) map[string]json.RawMessage {
	t.Helper()
	var u models.Usage
	if err := json.Unmarshal([]byte(usage), &u); err != nil {
		t.Fatalf("parsing usage %s: %v", usage, err)
	}
	return u.Extra
}

func TestReadReportedCost(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name     string
		vendor   string
		usage    string
		wantUSD  float64
		wantUnit string
		wantBYOK bool
		wantOK   bool
	}{
		// OpenRouter: usage.cost in credits.
		{"openrouter cost", "openrouter", `{"prompt_tokens":10,"completion_tokens":5,"cost":0.00042}`, 0.00042, UnitOpenRouterCredits, false, true},
		{"openrouter zero is a cost", "openrouter", `{"prompt_tokens":10,"cost":0}`, 0, UnitOpenRouterCredits, false, true},
		{"openrouter exponent", "openrouter", `{"cost":4.2e-4}`, 0.00042, UnitOpenRouterCredits, false, true},
		{"openrouter missing", "openrouter", `{"prompt_tokens":10}`, 0, "", false, false},
		{"openrouter null", "openrouter", `{"cost":null}`, 0, "", false, false},
		{"openrouter string", "openrouter", `{"cost":"0.00042"}`, 0, "", false, false},
		{"openrouter negative", "openrouter", `{"cost":-0.1}`, 0, "", false, false},
		{"openrouter overflow is not finite", "openrouter", `{"cost":1e999}`, 0, "", false, false},
		{"openrouter bool", "openrouter", `{"cost":true}`, 0, "", false, false},
		{"openrouter object", "openrouter", `{"cost":{"total_cost":1}}`, 0, "", false, false},
		// OpenRouter BYOK: usage.cost is only OpenRouter's fee.
		{"openrouter byok upstream cost", "openrouter", `{"cost":0.00001,"is_byok":true,"cost_details":{"upstream_inference_cost":0.0021}}`, 0.0021, UnitOpenRouterCredits, true, true},
		{"openrouter byok upstream zero", "openrouter", `{"cost":0.00001,"is_byok":true,"cost_details":{"upstream_inference_cost":0}}`, 0, UnitOpenRouterCredits, true, true},
		{"openrouter byok without upstream cost", "openrouter", `{"cost":0.00001,"is_byok":true}`, 0, "", true, false},
		{"openrouter byok upstream null", "openrouter", `{"cost":0.00001,"is_byok":true,"cost_details":{"upstream_inference_cost":null}}`, 0, UnitOpenRouterCredits, true, false},
		{"openrouter byok upstream negative", "openrouter", `{"cost":0.00001,"is_byok":true,"cost_details":{"upstream_inference_cost":-1}}`, 0, UnitOpenRouterCredits, true, false},
		{"openrouter byok false uses cost", "openrouter", `{"cost":0.00042,"is_byok":false,"cost_details":{"upstream_inference_cost":0.9}}`, 0.00042, UnitOpenRouterCredits, false, true},
		{"openrouter byok as a string is not byok", "openrouter", `{"cost":0.00042,"is_byok":"true"}`, 0.00042, UnitOpenRouterCredits, false, true},

		// Perplexity Responses API shape (shape and values from the
		// docs.perplexity.ai example with web_search and fetch_url).
		{"perplexity responses", "perplexity", `{"input_tokens":12088,"output_tokens":2743,"cost":{"cache_read_cost":0.00059,"currency":"USD","input_cost":0.00919,"output_cost":0.02743,"tool_calls_cost":0.0055,"total_cost":0.04271}}`, 0.04271, UnitUSD, false, true},
		{"perplexity responses null siblings", "perplexity", `{"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"tool_calls_cost":null,"tool_calls_cost_details":null,"cache_read_cost":null,"cache_creation_cost":null,"total_cost":0.003}}`, 0.003, UnitUSD, false, true},
		{"perplexity responses zero", "perplexity", `{"cost":{"currency":"USD","input_cost":0,"output_cost":0,"total_cost":0}}`, 0, UnitUSD, false, true},
		{"perplexity EUR", "perplexity", `{"cost":{"currency":"EUR","input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`, 0, "", false, false},
		{"perplexity lower-case usd is not USD", "perplexity", `{"cost":{"currency":"usd","input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`, 0, "", false, false},
		{"perplexity chat with a null currency", "perplexity", `{"cost":{"currency":null,"input_tokens_cost":0.001,"output_tokens_cost":0.002,"total_cost":0.003}}`, 0, "", false, false},
		{"perplexity responses with a null currency", "perplexity", `{"cost":{"currency":null,"input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`, 0, "", false, false},
		{"perplexity currency not a string", "perplexity", `{"cost":{"currency":1,"input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`, 0, "", false, false},
		{"perplexity responses shape without currency", "perplexity", `{"cost":{"input_cost":0.001,"output_cost":0.002,"total_cost":0.003}}`, 0, "", false, false},
		{"perplexity total null", "perplexity", `{"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":null}}`, 0, UnitUSD, false, false},
		{"perplexity total missing", "perplexity", `{"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002}}`, 0, UnitUSD, false, false},
		{"perplexity total string", "perplexity", `{"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":"0.003"}}`, 0, UnitUSD, false, false},
		{"perplexity total negative", "perplexity", `{"cost":{"currency":"USD","input_cost":0.001,"output_cost":0.002,"total_cost":-0.003}}`, 0, UnitUSD, false, false},
		{"perplexity cost a number", "perplexity", `{"cost":0.003}`, 0, "", false, false},
		{"perplexity cost a string", "perplexity", `{"cost":"0.003"}`, 0, "", false, false},
		{"perplexity cost an array", "perplexity", `{"cost":[0.003]}`, 0, "", false, false},
		{"perplexity cost null", "perplexity", `{"cost":null}`, 0, "", false, false},
		{"perplexity no cost", "perplexity", `{"prompt_tokens":5}`, 0, "", false, false},
		// Perplexity Chat Completions shape: no currency, documented as USD.
		// total_cost already includes request_cost (docs.perplexity.ai example).
		{"perplexity chat", "perplexity", `{"prompt_tokens":8,"completion_tokens":439,"total_tokens":447,"search_context_size":"low","cost":{"input_tokens_cost":2.4e-05,"output_tokens_cost":0.006585,"request_cost":0.006,"total_cost":0.012609}}`, 0.012609, UnitUSD, false, true},
		{"perplexity chat null siblings", "perplexity", `{"cost":{"input_tokens_cost":0.001,"output_tokens_cost":0.002,"request_cost":null,"reasoning_tokens_cost":null,"citation_tokens_cost":null,"search_queries_cost":null,"total_cost":0.003}}`, 0.003, UnitUSD, false, true},
		{"perplexity chat with USD", "perplexity", `{"cost":{"currency":"USD","input_tokens_cost":0.001,"output_tokens_cost":0.002,"total_cost":0.003}}`, 0.003, UnitUSD, false, true},
		{"perplexity chat with EUR", "perplexity", `{"cost":{"currency":"EUR","input_tokens_cost":0.001,"output_tokens_cost":0.002,"total_cost":0.003}}`, 0, "", false, false},
		{"perplexity unknown shape without currency", "perplexity", `{"cost":{"total_cost":0.003}}`, 0, "", false, false},

		// xAI: cost_in_usd_ticks, 1e10 ticks to the dollar (docs.x.ai).
		{"xai ticks", "xai", `{"prompt_tokens":10,"cost_in_usd_ticks":37756000}`, 0.0037756, UnitUSDTicks, false, true},
		{"xai zero", "xai", `{"cost_in_usd_ticks":0}`, 0, UnitUSDTicks, false, true},
		{"xai one dollar", "xai", `{"cost_in_usd_ticks":10000000000}`, 1, UnitUSDTicks, false, true},
		{"xai negative", "xai", `{"cost_in_usd_ticks":-1}`, 0, "", false, false},
		{"xai fraction", "xai", `{"cost_in_usd_ticks":1.5}`, 0, "", false, false},
		{"xai string", "xai", `{"cost_in_usd_ticks":"37756000"}`, 0, "", false, false},
		{"xai null", "xai", `{"cost_in_usd_ticks":null}`, 0, "", false, false},
		{"xai missing", "xai", `{"prompt_tokens":10}`, 0, "", false, false},
		{"xai too large", "xai", `{"cost_in_usd_ticks":99999999999999999999}`, 0, "", false, false},
		{"xai ignores a cost field", "xai", `{"cost":0.5}`, 0, "", false, false},

		// A vendor without a reader is never read.
		{"unknown vendor", "deepinfra", `{"cost":0.5}`, 0, "", false, false},
		{"openai", "openai", `{"cost":0.5}`, 0, "", false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			usd, unit, byok, ok := readReportedCost(tc.vendor, usageExtra(t, tc.usage))
			if ok != tc.wantOK || byok != tc.wantBYOK {
				t.Fatalf("ok = %v byok = %v, want ok = %v byok = %v", ok, byok, tc.wantOK, tc.wantBYOK)
			}
			if !ok {
				return
			}
			if unit != tc.wantUnit {
				t.Errorf("unit = %q, want %q", unit, tc.wantUnit)
			}
			if got, want := fmt.Sprintf("%.10f", usd), fmt.Sprintf("%.10f", tc.wantUSD); got != want {
				t.Errorf("usd = %s, want %s", got, want)
			}
		})
	}
}

func TestReadReportedCost_NoExtra(t *testing.T) {
	t.Parallel()
	for _, vendor := range providers.ReportedCostVendors() {
		if _, _, _, ok := readReportedCost(vendor, nil); ok {
			t.Errorf("%s: a usage with no extras read as a cost", vendor)
		}
	}
}

// Every vendor the trust rule can name has a reader, and no reader exists for
// a vendor the rule never names.
func TestReportedCostReadersMatchTheTrustedVendors(t *testing.T) {
	t.Parallel()
	vendors := providers.ReportedCostVendors()
	if len(vendors) != len(reportedCostReaders) {
		t.Fatalf("trusted vendors %v, readers for %d vendors", vendors, len(reportedCostReaders))
	}
	for _, v := range vendors {
		if _, ok := reportedCostReaders[v]; !ok {
			t.Errorf("no reader for trusted vendor %q", v)
		}
	}
}
