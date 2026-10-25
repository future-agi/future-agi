package cost

import (
	"log/slog"
	"sync"

	feedata "github.com/futureagi/agentcc-gateway/catalog/servertools"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/servertools"
)

// FeeLine is one server-tool count of an attempt and what it costs.
type FeeLine struct {
	Tool servertools.Tool
	Unit string
	// Count is the calls, queries, grounded prompts or containers priced.
	Count int
	// Minutes is a container's billed minutes, its minimum included.
	Minutes    int
	USDPerUnit float64
	// Micros is the line's fee, rounded half up.
	Micros int64
	// InputTokens are input tokens the fee adds at the model's input price
	// (OpenAI's fixed search block on mini models), which the caller prices.
	InputTokens int
	// Estimated marks a count or duration the gateway assumed: a container
	// whose duration or size the upstream did not report.
	Estimated bool
	// Waived is a fee the request's own tools make free.
	Waived bool
	// Unpriced is a count no fee row prices: it bills nothing.
	Unpriced bool
}

// PriceServerTools prices the provider-run tools u counts on
// fees, for model of vendor provider ("anthropic", "openai", "google";
// servertools.Vendor), in integer micros: each line is rounded half up once
// and upstreamMicros is their sum. It is the attempt's upstream cost on top
// of tokens, before the managed markup: a managed attempt bills it × (10,000 +
// markup bps) / 10,000, a BYOK attempt carries it as an upstream estimate.
// It bills at list and never assumes a free allowance. A count with no fee
// row bills nothing and is returned Unpriced.
func PriceServerTools(u *models.ServerToolUsage, provider, model string, fees servertools.FeeTable) (upstreamMicros int64, lines []FeeLine) {
	if u == nil {
		return 0, nil
	}
	add := func(l FeeLine) {
		upstreamMicros += l.Micros
		lines = append(lines, l)
	}
	for _, c := range []struct {
		tool  servertools.Tool
		count int
	}{
		{servertools.WebSearch, u.WebSearchRequests},
		{servertools.WebSearchPreview, u.WebSearchPreviewRequests},
		{servertools.WebFetch, u.WebFetchRequests},
		{servertools.FileSearch, u.FileSearchRequests},
	} {
		if c.count > 0 {
			add(countLine(fees, provider, model, c.tool, c.count))
		}
	}
	if u.WebSearchQueries > 0 || u.GroundedPrompts > 0 {
		add(groundingLine(fees, provider, model, servertools.GoogleSearch, u.WebSearchQueries, u.GroundedPrompts))
	}
	if u.MapsGroundedPrompts > 0 {
		add(groundingLine(fees, provider, model, servertools.GoogleMaps, u.MapsGroundedPrompts, u.MapsGroundedPrompts))
	}
	containers := u.Containers
	if len(containers) == 0 && u.CodeExecutionRequests > 0 {
		containers = []models.ServerToolContainer{{}} // ran in one container the response did not name
	}
	for _, c := range containers {
		add(containerLine(fees, provider, model, c))
	}
	return upstreamMicros, lines
}

// countLine prices count units of a per-call tool.
func countLine(fees servertools.FeeTable, provider, model string, tool servertools.Tool, count int) FeeLine {
	l := FeeLine{Tool: tool, Unit: servertools.UnitRequest, Count: count}
	f, ok := fees.Lookup(provider, tool, model, 0)
	if !ok {
		l.Unpriced = true
		return l
	}
	return priceUnits(fees, f, l)
}

// groundingLine prices Gemini grounding on the row model takes: per search
// query (Gemini 3 and later) or per grounded prompt (2.5 and earlier).
func groundingLine(fees servertools.FeeTable, provider, model string, tool servertools.Tool, queries, prompts int) FeeLine {
	l := FeeLine{Tool: tool}
	f, ok := fees.Lookup(provider, tool, model, 0)
	if !ok {
		l.Unit, l.Count, l.Unpriced = servertools.UnitGroundedPrompt, prompts, true
		return l
	}
	l.Count = prompts
	if f.Unit == servertools.UnitQuery {
		l.Count = queries
	}
	return priceUnits(fees, f, l)
}

// priceUnits prices l.Count units on f.
func priceUnits(fees servertools.FeeTable, f *servertools.Fee, l FeeLine) FeeLine {
	l.Unit, l.USDPerUnit = f.Unit, f.USDPerUnit
	if fees.Waived(f) {
		l.Waived = true
		return l
	}
	l.Micros = usdToMicros(float64(l.Count) * f.USDPerUnit)
	l.InputTokens = l.Count * f.InputTokensPerUnit
	return l
}

// containerLine prices one container: its minutes, at least the fee's
// minimum, at the per-minute price of its size. A duration or size the
// upstream did not report is the minimum or the smallest size, an estimate.
func containerLine(fees servertools.FeeTable, provider, model string, c models.ServerToolContainer) FeeLine {
	tool := servertools.Container
	if provider == servertools.VendorAnthropic {
		tool = servertools.CodeExecution
	}
	l := FeeLine{Tool: tool, Count: 1, Minutes: c.Minutes, Estimated: c.Minutes <= 0}
	size := c.MemoryGB
	if tool == servertools.Container && size <= 0 {
		size, l.Estimated = fees.SmallestContainerGB(provider), true
	}
	f, ok := fees.Lookup(provider, tool, model, size)
	if !ok {
		l.Unit, l.Unpriced = servertools.UnitContainerSession, true
		return l
	}
	l.Unit, l.USDPerUnit = f.Unit, f.USDPerUnit
	l.Minutes = max(l.Minutes, f.MinMinutes)
	if fees.Waived(f) {
		l.Waived = true
		return l
	}
	l.Micros = usdToMicros(float64(l.Minutes) * f.USDPerMinute())
	return l
}

// shippedServerToolFees is the fee table the binary ships. A table that
// does not parse (a build TestShippedFeeTable refuses) prices nothing, and
// says so once.
var shippedServerToolFees = sync.OnceValue(func() servertools.FeeTable {
	fees, err := feedata.Fees()
	if err != nil {
		slog.Error("server-tool fee table does not parse; server tools are not priced", "error", err)
	}
	return fees
})

// addServerToolFees adds the server-tool fees of the attempt that served
// to its cost c: usage is the attempt's own usage and card the
// card its tokens were priced on (priced reports whether they were). A
// response with Gemini Grounded Results marks the request eval_export
// forbidden. A charge the upstream reported already holds the fees, and an
// attempt whose tokens have no price stays unpriced; searches the card
// itself prices (its WebSearch price) are not billed twice, though their
// fixed input block (OpenAI mini models) still is. A BYOK fee is the
// gateway's estimate of the customer's own upstream bill, so it marks the
// attempt usage_estimated.
func (p *Plugin) addServerToolFees(rc *models.RequestContext, a *models.Attempt, usage *models.Usage, card models.PriceCard, priced bool, c *attemptCost) {
	if usage == nil || usage.ServerToolUse == nil {
		return
	}
	u := usage.ServerToolUse
	if servertools.GeminiGrounded(u) {
		rc.SetLogEvidence("eval_export", "forbidden")
	}
	if !priced || c.source == models.CostSourceProviderReported {
		return
	}
	model := a.Model
	if model == "" {
		model = rc.Model
	}
	vendor := servertools.Vendor(a.Provider, model)
	micros, lines := PriceServerTools(u, vendor, model, shippedServerToolFees().ForRequest(rc.Request))
	estimated, tokens := false, 0
	for _, l := range lines {
		if l.Tool == servertools.WebSearch && card.WebSearch > 0 {
			// PriceAttempt priced the calls from the card; the input block stays.
			micros, l.Unpriced = micros-l.Micros, false
		}
		estimated = estimated || (l.Estimated && !l.Waived && !l.Unpriced)
		tokens += l.InputTokens
		if l.Unpriced && c.managed {
			slog.Warn("managed attempt used a server tool with no fee; it was not billed",
				"request_id", rc.RequestID, "provider", a.Provider, "model", model, "tool", l.Tool)
		}
	}
	micros += usdToMicros(float64(tokens) * card.Prompt)
	if micros == 0 && !estimated {
		return
	}
	c.upstream += micros
	c.billed, c.landed = billedAndLanded(c.upstream, c.managed, p.markupBps, c.upstreamFeeBps, c.processingFeeBps)
	c.estimated = c.estimated || estimated || !c.managed
}
