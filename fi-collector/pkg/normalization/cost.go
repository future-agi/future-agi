package normalization

import (
	"math"
	"slices"
	"strconv"
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func CostCoverageError() string { return registry.CostCoverageError }

// IngestionHold checks the original batch before routing can drop a marked
// span. It is read-only: normalization growth belongs to the budgeted pass.
func IngestionHold(td ptrace.Traces) string {
	if RequiresAdmission(td) {
		return AdmissionError()
	}
	rss := td.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		scopes := rss.At(i).ScopeSpans()
		for j := 0; j < scopes.Len(); j++ {
			ss := scopes.At(j)
			profile := ProfileFor(ss.Scope())
			for k := 0; k < ss.Spans().Len(); k++ {
				attrs := ss.Spans().At(k).Attributes()
				if profile == nil && String(attrs, "fi.normalization.profile") == "" {
					continue
				}
				// The qualified summary will normalize to contribution=none.
				if NonAdditive(attrs) || (profile != nil && slices.Contains(profile.SummaryOperations, String(attrs, "gen_ai.operation.name"))) {
					continue
				}
				if hasDetailedUsage(attrs) && !hasCompleteCost(attrs) {
					return CostCoverageError()
				}
			}
		}
	}
	return ""
}

// Require a total or both components in aliases supported by BOTH ingestion
// paths. A lone component, malformed charge or sender-only cost alias cannot
// certify a complete price for cache/reasoning usage. Explicit USD zero is valid.
func hasCompleteCost(attrs pcommon.Map) bool {
	component := func(name string) (float64, bool, bool) {
		for _, key := range registry.DetailedCostKeys[name] {
			v, exists := attrs.Get(key)
			if !exists {
				continue
			}
			var value float64
			switch v.Type() {
			case pcommon.ValueTypeInt:
				value = float64(v.Int())
			case pcommon.ValueTypeDouble:
				value = v.Double()
			case pcommon.ValueTypeStr:
				var err error
				value, err = strconv.ParseFloat(strings.TrimSpace(v.Str()), 64)
				if err != nil {
					return 0, true, false
				}
			default:
				return 0, true, false
			}
			return value, true, !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0
		}
		return 0, false, false
	}
	if _, present, valid := component("total"); present {
		return valid
	}
	input, _, inputValid := component("input")
	output, _, outputValid := component("output")
	return inputValid && outputValid && !math.IsInf(input+output, 0)
}
