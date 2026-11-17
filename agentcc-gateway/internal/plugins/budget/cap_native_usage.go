package budget

import (
	"bytes"
	"encoding/json"
	"math"
	"reflect"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ObserveNative accepts only the concrete issued handle and a pinned decoder's
// correlated evidence. Neither HTTP success, EOF nor Complete is a terminal
// qualification. Any conflicting observation makes this admission uncertain.
func ObserveNative(handle NativeAttempt, event NativeUsageEvent) *models.APIError {
	a, ok := handle.(*nativeAttemptState)
	if !ok || a == nil || a.owner == nil {
		return capStateUnavailable()
	}
	c := a.owner
	c.mu.Lock()
	defer c.mu.Unlock()
	bad := func() *models.APIError { c.failed = true; return capStateUnavailable() }
	if c.rc == nil || c.attempt != a || c.closing || c.failed || a.ending || a.ended || c.quote == nil {
		return bad()
	}
	if event.FinancialID != a.financialID || event.Kind != c.quote.bound.kind || event.Sequence == 0 || event.Generation.Conflict || event.Generation.ID == "" || event.Generation.ServedModel != c.binding.SentModel || event.Generation.Provider != c.binding.UpstreamProvider {
		return bad()
	}
	event.Usage = nativeCopyUsage(event.Usage)
	if !nativeUsageValid(event.Usage) {
		return bad()
	}
	if event.Sequence == a.sequence {
		if reflect.DeepEqual(event, a.last) {
			return nil
		}
		return bad()
	}
	if a.terminal || event.Sequence != a.sequence+1 || (a.generation.ID != "" && a.generation != event.Generation) {
		return bad()
	}
	original := event
	original.Usage = nativeCopyUsage(event.Usage)
	var merged *models.Usage
	switch event.Kind {
	case NativeCumulativeUsage:
		if !nativeMonotonic(a.usage, event.Usage) {
			return bad()
		}
		merged = models.MergeUsage(nativeCopyUsage(a.usage), nativeCopyUsage(event.Usage))
	case NativeDeltaUsage:
		var valid bool
		merged, valid = nativeAddUsage(a.usage, event.Usage)
		if !valid {
			return bad()
		}
	default:
		return bad()
	}
	if c.quote.bound.meter == nil {
		return bad()
	}
	event.Usage = nativeCopyUsage(merged)
	qualified, complete := c.quote.bound.meter(nativeCopyUsage(a.usage), event)
	// The qualifier may validate coverage and ceilings, but cannot silently
	// change the accumulated counters or invent a terminal from a partial event.
	if !nativeUsageValid(qualified) || !reflect.DeepEqual(qualified, merged) || (complete && !event.Complete) {
		return bad()
	}
	a.usage, a.generation = nativeCopyUsage(qualified), event.Generation
	a.sequence, a.last, a.terminal = event.Sequence, original, complete
	return nil
}

func EndNative(handle NativeAttempt, reason NativeEnd) *models.APIError {
	a, ok := handle.(*nativeAttemptState)
	if !ok || a == nil || a.owner == nil {
		return capStateUnavailable()
	}
	c := a.owner
	c.mu.Lock()
	if c.rc == nil || c.attempt != a {
		c.mu.Unlock()
		return capStateUnavailable()
	}
	if a.ended {
		valid := reason == a.end && !c.failed
		c.mu.Unlock()
		if !valid {
			return capStateUnavailable()
		}
		return nil
	}
	if a.ending {
		c.mu.Unlock()
		return capStateUnavailable()
	}
	if reason < NativeCompleted || reason > NativeProviderError {
		c.failed = true
		reason = NativeAborted
	}
	a.ending, a.end = true, reason
	finish := a.finish
	a.finish = nil // no callback can survive End or execute twice
	usage, generation := nativeCopyUsage(a.usage), a.generation
	var failure *models.APIError
	if c.failed || !a.terminal || reason != NativeCompleted {
		c.failed = true
		failure = capStateUnavailable()
	}
	c.mu.Unlock()
	finished := false
	defer func() {
		c.mu.Lock()
		defer c.mu.Unlock()
		a.ending, a.ended = false, true
		if !finished {
			c.failed = true
		}
	}()
	if finish != nil {
		finish(usage, generation, failure)
	} else {
		return capStateUnavailable()
	}
	finished = true
	return failure
}

func nativeCopyUsage(u *models.Usage) *models.Usage {
	if u == nil {
		return nil
	}
	v := *u // retain private decoded presence flags, unlike a JSON round trip
	copyFloat := func(p *float64) *float64 {
		if p == nil {
			return nil
		}
		n := *p
		return &n
	}
	copyRaw := func(p *json.RawMessage) *json.RawMessage {
		if p == nil {
			return nil
		}
		r := json.RawMessage(bytes.Clone(*p))
		return &r
	}
	v.Cost = copyFloat(u.Cost)
	v.PromptTokensDetails, v.CompletionTokensDetails = copyRaw(u.PromptTokensDetails), copyRaw(u.CompletionTokensDetails)
	if u.IsByok != nil {
		b := *u.IsByok
		v.IsByok = &b
	}
	if u.CostDetails != nil {
		d := *u.CostDetails
		d.UpstreamInferenceCost = copyFloat(d.UpstreamInferenceCost)
		d.UpstreamInferencePromptCost = copyFloat(d.UpstreamInferencePromptCost)
		d.UpstreamInferenceCompletionsCost = copyFloat(d.UpstreamInferenceCompletionsCost)
		v.CostDetails = &d
	}
	if u.ServerToolUse != nil {
		d := *u.ServerToolUse
		d.Containers = append([]models.ServerToolContainer(nil), d.Containers...)
		v.ServerToolUse = &d
	}
	if u.Media != nil {
		d := *u.Media
		if d.OutputImages != nil {
			n := *d.OutputImages
			d.OutputImages = &n
		}
		v.Media = &d
	}
	if u.Extra != nil {
		v.Extra = make(map[string]json.RawMessage, len(u.Extra))
		for k, raw := range u.Extra {
			v.Extra[k] = append(json.RawMessage(nil), raw...)
		}
	}
	return &v
}

// nativeFinishUsage derives the representation recorded by attemptRun.finish on
// a detached copy. Raw bytes and decoded presence stay intact. A zero sidecar
// may be unparsed; a populated sidecar must already agree with its raw object.
// Check before parsing so normalization cannot erase contradictory evidence.
func nativeFinishUsage(u *models.Usage) (*models.Usage, bool) {
	if u == nil {
		return nil, false
	}
	v := nativeCopyUsage(u)
	object := func(raw json.RawMessage) bool {
		raw = bytes.TrimSpace(raw)
		return len(raw) > 0 && raw[0] == '{'
	}
	if raw := u.PromptTokensDetails; raw != nil {
		var d models.PromptTokensDetails
		if !object(*raw) || json.Unmarshal(*raw, &d) != nil || (u.PromptDetails != (models.PromptTokensDetails{}) && u.PromptDetails != d) {
			return nil, false
		}
		v.PromptDetails = d
	}
	if raw := u.CompletionTokensDetails; raw != nil {
		var d models.CompletionTokensDetails
		if !object(*raw) || json.Unmarshal(*raw, &d) != nil || (u.CompletionDetails != (models.CompletionTokensDetails{}) && u.CompletionDetails != d) {
			return nil, false
		}
		v.CompletionDetails = d
	}
	return v, true
}

func nativeUsageValid(u *models.Usage) bool {
	if u == nil || u.Estimated || u.CostContested() || (u.Cost != nil && !u.HasReportedCost()) {
		return false
	}
	if u.Media != nil && (u.Media.IdentityConflict || u.Media.InvalidImage) {
		return false
	}
	// Inspect known numeric counters including nested media/tools and costs.
	// Unknown raw dimensions are the pinned decoder's responsibility, not free.
	var valid func(reflect.Value) bool
	valid = func(v reflect.Value) bool {
		switch v.Kind() {
		case reflect.Pointer:
			return v.IsNil() || valid(v.Elem())
		case reflect.Struct:
			for i := 0; i < v.NumField(); i++ {
				if v.Type().Field(i).IsExported() && !valid(v.Field(i)) {
					return false
				}
			}
		case reflect.Int, reflect.Int64:
			return v.Int() >= 0 && v.Int() <= 9_007_199_254_740_991
		case reflect.Float64:
			return !math.IsNaN(v.Float()) && !math.IsInf(v.Float(), 0) && v.Float() >= 0
		case reflect.Slice:
			if v.Type().Elem().Kind() == reflect.Struct {
				for i := 0; i < v.Len(); i++ {
					if !valid(v.Index(i)) {
						return false
					}
				}
			}
		}
		return true
	}
	copy, ok := nativeFinishUsage(u)
	return ok && valid(reflect.ValueOf(u)) && valid(reflect.ValueOf(copy))
}

func nativeMonotonic(previous, next *models.Usage) bool {
	if previous == nil {
		return true
	}
	if next == nil {
		return false
	}
	if previous.HasPromptTokens() && next.HasPromptTokens() && next.PromptTokens < previous.PromptTokens {
		return false
	}
	if previous.HasCompletionTokens() && next.HasCompletionTokens() && next.CompletionTokens < previous.CompletionTokens {
		return false
	}
	if previous.HasReportedCost() && next.HasReportedCost() && *next.Cost < *previous.Cost {
		return false
	}
	// A present details object is cumulative too. Absence is handled by
	// MergeUsage; an explicit lower value is conflicting, not a reset.
	var monotone func(reflect.Value, reflect.Value) bool
	monotone = func(a, b reflect.Value) bool {
		switch a.Kind() {
		case reflect.Pointer:
			if a.IsNil() || b.IsNil() {
				return true
			}
			return monotone(a.Elem(), b.Elem())
		case reflect.Struct:
			for i := 0; i < a.NumField(); i++ {
				if a.Type().Field(i).IsExported() && !monotone(a.Field(i), b.Field(i)) {
					return false
				}
			}
		case reflect.Int, reflect.Int64:
			return b.Int() >= a.Int()
		}
		return true
	}
	a, b := nativeCopyUsage(previous), nativeCopyUsage(next)
	a.ParseDetails()
	b.ParseDetails()
	if next.PromptTokensDetails != nil && !monotone(reflect.ValueOf(a.PromptDetails), reflect.ValueOf(b.PromptDetails)) {
		return false
	}
	if next.CompletionTokensDetails != nil && !monotone(reflect.ValueOf(a.CompletionDetails), reflect.ValueOf(b.CompletionDetails)) {
		return false
	}
	return monotone(reflect.ValueOf(a.ServerToolUse), reflect.ValueOf(b.ServerToolUse)) && monotone(reflect.ValueOf(a.Media), reflect.ValueOf(b.Media))
}

// Only the explicitly qualified token-only delta subset is normalized here.
// Monetary/media/tool/extra deltas require a separate owner-qualified contract.
func nativeAddUsage(a, b *models.Usage) (*models.Usage, bool) {
	if b == nil {
		return nil, false
	}
	for _, u := range []*models.Usage{a, b} {
		if u != nil && (u.Cost != nil || u.CostDecimal != "" || u.CostDetails != nil || u.Media != nil || u.ServerToolUse != nil || len(u.Extra) != 0 || u.PromptTokensDetails != nil || u.CompletionTokensDetails != nil || u.PromptDetails != (models.PromptTokensDetails{}) || u.CompletionDetails != (models.CompletionTokensDetails{})) {
			return nil, false
		}
	}
	if a == nil {
		return nativeCopyUsage(b), true
	}
	add := func(x, y int) (int, bool) {
		if x < 0 || y < 0 || x > math.MaxInt-y || int64(x) > 9_007_199_254_740_991-int64(y) {
			return 0, false
		}
		return x + y, true
	}
	p, pok := add(a.PromptTokens, b.PromptTokens)
	out, ook := add(a.CompletionTokens, b.CompletionTokens)
	total, tok := add(a.TotalTokens, b.TotalTokens)
	if !pok || !ook || !tok {
		return nil, false
	}
	var pp, op *int
	if a.HasPromptTokens() || b.HasPromptTokens() {
		pp = &p
	}
	if a.HasCompletionTokens() || b.HasCompletionTokens() {
		op = &out
	}
	result := models.NewUsage(pp, op)
	result.TotalTokens = total
	return result, true
}
