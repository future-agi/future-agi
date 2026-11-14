package cost

import (
	"bytes"
	"encoding/json"
	"math"
	"strconv"
)

// Some vendors state in their response what the request cost, in a unit the
// gateway knows. For those (see providers.ReportedCostVendor for which
// providers are trusted) the stated cost is the request's cost, in place of
// the catalogue's list-price estimate. Every value is read from the usage
// keys the gateway does not name, which models.Usage keeps in Extra and
// passes back to the client unchanged.

// Units recorded in Metadata["cost_reported_unit"]: what the vendor stated
// the cost in, before it was converted to USD.
const (
	// UnitOpenRouterCredits is OpenRouter's usage.cost, in credits, which
	// OpenRouter sells at one USD each.
	UnitOpenRouterCredits = "openrouter_credits"
	// UnitUSD is a cost stated in US dollars (Perplexity's usage.cost).
	UnitUSD = "usd"
	// UnitUSDTicks is xAI's usage.cost_in_usd_ticks, 1e10 ticks to the dollar.
	UnitUSDTicks = "usd_ticks"
)

// usdTicksPerUSD is how many of xAI's ticks make one US dollar
// (docs.x.ai, cost tracking).
const usdTicksPerUSD = 1e10

// reportedCostReaders reads each trusted vendor's stated cost. A reader
// returns the cost in USD, the unit the vendor stated it in, whether the
// call ran on the caller's own key at the vendor (OpenRouter BYOK), and
// whether the cost was usable. A cost that is missing, null, not a number,
// non-finite or negative is not usable, and the catalogue prices the
// request. Zero is a real cost.
var reportedCostReaders = map[string]func(extra map[string]json.RawMessage) (usd float64, unit string, byok, ok bool){
	"openrouter": readOpenRouterCost,
	"perplexity": readPerplexityCost,
	"xai":        readXAICost,
}

// MaxReportedCostUSD is the most a vendor's stated cost may be for one request.
// Above it the request is priced from the catalogue: no single chat request
// costs more than $1,000, and a value like 1e13 would overflow the int64
// micro-dollar ledgers (credits, budgets, metrics).
const MaxReportedCostUSD = 1000.0

// readReportedCost reads vendor's stated cost from a response's usage extras.
func readReportedCost(vendor string, extra map[string]json.RawMessage) (usd float64, unit string, byok, ok bool) {
	read, known := reportedCostReaders[vendor]
	if !known || len(extra) == 0 {
		return 0, "", false, false
	}
	return read(extra)
}

// readOpenRouterCost reads OpenRouter's usage.cost, in credits.
//
// When the call ran on the caller's own provider key at OpenRouter
// (is_byok: true), usage.cost is only OpenRouter's fee; what the upstream
// charged is cost_details.upstream_inference_cost. That is the request's cost
// when it is usable, and otherwise the catalogue prices the request. Which of
// the two applies is decided by is_byok, never by the amounts.
func readOpenRouterCost(extra map[string]json.RawMessage) (float64, string, bool, bool) {
	if isTrue(extra["is_byok"]) {
		var details map[string]json.RawMessage
		if !decodeObject(extra["cost_details"], &details) {
			return 0, "", true, false
		}
		usd, ok := nonNegativeNumber(details["upstream_inference_cost"])
		return usd, UnitOpenRouterCredits, true, ok
	}
	usd, ok := nonNegativeNumber(extra["cost"])
	return usd, UnitOpenRouterCredits, false, ok
}

// readPerplexityCost reads Perplexity's usage.cost object, whose total_cost
// already includes the search, tool, request and cache costs listed beside
// it, so it is used alone and nothing is added to it.
//
// The Agent/Responses API states the currency ({currency: "USD", input_cost,
// output_cost, …, total_cost}); a cost in that shape without a currency is
// not used. The Sonar Chat Completions API states none ({input_tokens_cost,
// output_tokens_cost, …, total_cost}), and documents every value as USD. Any
// stated currency other than USD, null included, is not used.
func readPerplexityCost(extra map[string]json.RawMessage) (float64, string, bool, bool) {
	var cost map[string]json.RawMessage
	if !decodeObject(extra["cost"], &cost) {
		return 0, "", false, false
	}
	if raw, stated := cost["currency"]; stated {
		// A stated currency, null included, must be USD.
		var currency string
		if isNull(raw) || json.Unmarshal(raw, &currency) != nil || currency != "USD" {
			return 0, "", false, false
		}
	} else if !perplexityChatShape(cost) {
		return 0, "", false, false
	}
	usd, ok := nonNegativeNumber(cost["total_cost"])
	return usd, UnitUSD, false, ok
}

// perplexityChatShape reports whether cost is shaped as the Chat Completions
// API's, which states no currency, rather than the Responses API's, which
// must.
func perplexityChatShape(cost map[string]json.RawMessage) bool {
	_, responsesIn := cost["input_cost"]
	_, responsesOut := cost["output_cost"]
	if responsesIn || responsesOut {
		return false
	}
	_, chatIn := cost["input_tokens_cost"]
	_, chatOut := cost["output_tokens_cost"]
	return chatIn || chatOut
}

// readXAICost reads xAI's usage.cost_in_usd_ticks, a whole number of ticks.
func readXAICost(extra map[string]json.RawMessage) (float64, string, bool, bool) {
	raw := bytes.TrimSpace(extra["cost_in_usd_ticks"])
	if !isJSONNumber(raw) {
		return 0, "", false, false
	}
	ticks, err := strconv.ParseInt(string(raw), 10, 64)
	if err != nil || ticks < 0 {
		return 0, "", false, false
	}
	return float64(ticks) / usdTicksPerUSD, UnitUSDTicks, false, true
}

// nonNegativeNumber is raw as a finite number >= 0. A JSON string, null,
// boolean, object or array is not a number, even one that would parse.
func nonNegativeNumber(raw json.RawMessage) (float64, bool) {
	raw = bytes.TrimSpace(raw)
	if !isJSONNumber(raw) {
		return 0, false
	}
	v, err := strconv.ParseFloat(string(raw), 64)
	if err != nil || math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
		return 0, false
	}
	return v, true
}

// isJSONNumber reports whether raw is a JSON number literal.
func isJSONNumber(raw []byte) bool {
	if len(raw) == 0 || (raw[0] != '-' && (raw[0] < '0' || raw[0] > '9')) {
		return false
	}
	var n json.Number
	return json.Unmarshal(raw, &n) == nil
}

// decodeObject decodes raw into out when raw is a JSON object.
func decodeObject(raw json.RawMessage, out *map[string]json.RawMessage) bool {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || raw[0] != '{' {
		return false
	}
	return json.Unmarshal(raw, out) == nil
}

// isTrue reports whether raw is the JSON literal true.
func isTrue(raw json.RawMessage) bool {
	return string(bytes.TrimSpace(raw)) == "true"
}

// isNull reports whether raw is the JSON literal null.
func isNull(raw json.RawMessage) bool {
	return string(bytes.TrimSpace(raw)) == "null"
}
