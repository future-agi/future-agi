package toolcatalog

import (
	"errors"
	"fmt"
	"slices"
)

// termsFloor is what a vendor's terms (read 2026-10-15) require of every
// card of that vendor. A card may be stricter, never looser, so an
// operator's tools.catalog_dir cannot drop a constraint the seed cards
// carry. A floor changes only when the vendor grants in writing what its
// terms bar.
type termsFloor struct {
	maxCacheable, maxRetention, minTrains string // "" leaves the field free
	improvementLicense, noDivert          bool
	noPerfDisclosure, noBenchmark         bool
	evalForbidden                         bool
}

var termsFloors = map[string]termsFloor{
	// No copy beyond a browser cache; 1(c): improvement licence.
	"exa": {maxCacheable: CacheNone, maxRetention: RetentionMetadata, improvementLicense: true},
	// 2(b): output for one end customer, no serving prior outputs; 2(c): no
	// diverting, no benchmarks; 4(b): trains on inputs, improvement licence.
	"parallel": {maxCacheable: CacheNone, minTrains: TrainsYes, improvementLicense: true, noDivert: true, noBenchmark: true},
	// 3.2(x): no performance disclosure; 6.5: trains on AI-functionality
	// inputs; 9.2: improvement licence.
	"tavily": {minTrains: TrainsAIFeaturesOnly, improvementLicense: true, noPerfDisclosure: true},
	// 3(b)(i): transient storage only; 3(b)(xiii): no evaluating or
	// benchmarking AI models with results.
	"brave": {maxCacheable: CacheNone, maxRetention: RetentionMetadata, noBenchmark: true, evalForbidden: true},
	// No caching, analyzing or learning from grounded results.
	"gemini_grounding": {maxCacheable: CacheNone, evalForbidden: true},
	// 3.2: no cache or store, no train, test or validate.
	"google_maps": {maxCacheable: CacheNone, maxRetention: RetentionMetadata, evalForbidden: true},
	// Agentic Commerce 3.1: no training on agentic transaction data.
	"stripe": {evalForbidden: true},
	// The vendor may train on customer inputs (its terms).
	"bfl":      {minTrains: TrainsYes},
	"runway":   {minTrains: TrainsYes},
	"deepgram": {minTrains: TrainsYes},
	"gladia":   {minTrains: TrainsYes},
}

// Orders from most to least restrictive; a card's value may not come later
// in its order than the floor's.
var (
	cacheOrder     = []string{CacheNone, CacheOrg, CacheGlobal}
	retentionOrder = []string{RetentionNone, RetentionMetadata, RetentionFull}
	// For trains_on_input "stricter" means admitting more training, which
	// excludes the vendor from more no-training orgs.
	trainsOrder = []string{TrainsYes, TrainsAIFeaturesOnly, TrainsNo}
)

// checkFloor refuses a card looser than its vendor's terms floor.
func checkFloor(c *Card) error {
	f, ok := termsFloors[c.Vendor]
	if !ok {
		return nil
	}
	var errs []error
	looser := func(order []string, got, limit string) bool {
		return limit != "" && slices.Index(order, got) > slices.Index(order, limit)
	}
	if looser(cacheOrder, c.Cacheable, f.maxCacheable) {
		errs = append(errs, fmt.Errorf("cacheable %s is looser than %s's terms allow (%s)", c.Cacheable, c.Vendor, f.maxCacheable))
	}
	if looser(retentionOrder, c.ResultsRetention, f.maxRetention) {
		errs = append(errs, fmt.Errorf("results_retention %s is looser than %s's terms allow (%s)", c.ResultsRetention, c.Vendor, f.maxRetention))
	}
	if looser(trainsOrder, c.TrainsOnInput, f.minTrains) {
		errs = append(errs, fmt.Errorf("trains_on_input %s understates %s's terms (%s)", c.TrainsOnInput, c.Vendor, f.minTrains))
	}
	for _, flag := range []struct {
		name      string
		need, got bool
	}{
		{"improvement_license", f.improvementLicense, c.ImprovementLicense},
		{"no_divert", f.noDivert, c.NoDivert},
		{"no_perf_disclosure", f.noPerfDisclosure, c.NoPerfDisclosure},
		{"no_benchmark", f.noBenchmark, c.NoBenchmark},
	} {
		if flag.need && !flag.got {
			errs = append(errs, fmt.Errorf("%s must be true for %s (its terms)", flag.name, c.Vendor))
		}
	}
	if f.evalForbidden && c.EvalExport != EvalExportForbidden {
		errs = append(errs, fmt.Errorf("eval_export must be forbidden for %s (its terms)", c.Vendor))
	}
	if len(errs) > 0 {
		return fmt.Errorf("%s/%s: %w", c.Capability, c.Vendor, errors.Join(errs...))
	}
	return nil
}
