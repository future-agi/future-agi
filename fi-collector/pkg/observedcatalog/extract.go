package observedcatalog

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
)

// ScopedSpan carries the existing authenticated collector sidecar. Backfill
// must verify project/workspace membership before constructing it.
type ScopedSpan struct {
	OrganizationID string
	WorkspaceID    string
	ScopeError     string
	Row            map[string]any
}

type Limits struct {
	MaxKeysPerSpan         int
	MaxArrayMembersPerSpan int
}

func DefaultLimits() Limits { return Limits{MaxKeysPerSpan: 128, MaxArrayMembersPerSpan: 256} }

type Report struct {
	Complete   bool
	GapReasons []string
}

// Extract emits custom keys and selectable scalar values plus the code-owned
// model observation. It never mutates canonical rows. Its value eligibility
// limits are per row; the resulting batch is chunked, never byte-truncated.
func Extract(span ScopedSpan, limits Limits) (Batch, Report, error) {
	if limits == (Limits{}) {
		limits = DefaultLimits()
	}
	if limits.MaxKeysPerSpan <= 0 || limits.MaxKeysPerSpan > 4096 || limits.MaxArrayMembersPerSpan <= 0 || limits.MaxArrayMembersPerSpan > 16384 {
		return Batch{}, Report{}, errors.New("observedcatalog: invalid extraction limits")
	}
	project, _ := span.Row["project_id"].(string)
	scope := Scope{OrganizationID: span.OrganizationID, WorkspaceID: span.WorkspaceID, ProjectID: project}
	if err := validateScope(scope); err != nil {
		return Batch{}, Report{}, err
	}
	org, orgIsString := span.Row["org_id"].(string)
	if span.ScopeError != "" || (span.Row["org_id"] != nil && !orgIsString) || (org != "" && org != scope.OrganizationID) {
		return Batch{}, Report{}, errors.New("observedcatalog: authenticated scope mismatch")
	}
	seenText, _ := span.Row["start_time"].(string)
	seen, err := time.Parse(TimeLayout, seenText)
	if err != nil || seen.Format(TimeLayout) != seenText {
		return Batch{}, Report{}, errors.New("observedcatalog: noncanonical source timestamp")
	}
	attrs, err := canonicalMaps(span.Row)
	if err != nil {
		return Batch{}, Report{}, err
	}
	batch := Batch{Keys: []KeyRow{}, Values: []ValueRow{}}
	gaps := map[string]bool{}
	// Reject empty/oversized keys before they consume the selection budget.
	// Nonempty whitespace/control keys are exact identities, never normalized.
	rejectKey := func(key string) bool {
		if key == "" {
			gaps[attributecatalog.GapInvalidAttributeKey] = true
			return true
		}
		if len(key) > MaxKeyBytes {
			gaps["key_too_large"] = true
			return true
		}
		return false
	}
	for key := range attrs.Strings {
		if rejectKey(key) {
			delete(attrs.Strings, key)
		}
	}
	for key := range attrs.Numbers {
		if rejectKey(key) {
			delete(attrs.Numbers, key)
		}
	}
	for key := range attrs.Booleans {
		if rejectKey(key) {
			delete(attrs.Booleans, key)
		}
	}
	for key := range attrs.Extra {
		if rejectKey(key) {
			delete(attrs.Extra, key)
		}
	}
	build := func(maps attributecatalog.SpanAttributeMaps, kind string) error {
		// Builder row accounting includes raw search text, canonical JSON, key,
		// type and fingerprint. This per-row guard fits every eligible string,
		// including worst-case escaping; there is no cumulative byte truncation.
		maxValueRowBytes := MaxValueBytes + MaxStringValueBytes + MaxKeyBytes + 128
		built, err := attributecatalog.BuildObservedRows(attributecatalog.Scope{ProjectID: project, SeenAt: seen}, maps, attributecatalog.BuildLimits{MaxKeys: limits.MaxKeysPerSpan, MaxArrayMembers: limits.MaxArrayMembersPerSpan}, kind, maxValueRowBytes)
		if err != nil {
			return err
		}
		for _, reason := range built.Metadata.GapReasons {
			if reason == attributecatalog.GapMaxEncodedBytes {
				// The only byte guard in this builder mode is per value. Its
				// derived ceiling cannot reject an eligible raw string.
				reason = "value_too_large"
			}
			gaps[reason] = true
		}
		for _, row := range built.KeyRows {
			batch.Keys = append(batch.Keys, KeyRow{Scope: scope, SourceKind: kind, AttributeKey: row.AttributeKey, AttributeType: row.AttributeType, KeyFolded: fold(row.AttributeKey), FirstSeen: seenText, LastSeen: seenText})
		}
		for _, row := range built.ValueRows {
			if len(row.ValueJSON) > MaxValueBytes || len(row.ValueSearchText) > stringValueLimit(row.AttributeType) {
				gaps["value_too_large"] = true
				continue
			}
			search := fold(row.ValueSearchText)
			if len(search) > MaxSearchTextBytes {
				gaps["value_too_large"] = true
				continue
			}
			batch.Values = append(batch.Values, ValueRow{Scope: scope, SourceKind: kind, AttributeKey: row.AttributeKey, AttributeType: row.AttributeType, ValueFingerprint: row.ValueFingerprint, ValueJSON: row.ValueJSON, ValueSearchTextFolded: search, FirstSeen: seenText, LastSeen: seenText})
		}
		return nil
	}
	if err := build(attrs, attributecatalog.SourceKindCustomAttribute); err != nil {
		return Batch{}, Report{}, err
	}
	if model, ok := span.Row["model"].(string); ok && model != "" {
		if err := build(attributecatalog.SpanAttributeMaps{Strings: map[string]string{"model": model}}, attributecatalog.SourceKindSystemAttribute); err != nil {
			return Batch{}, Report{}, err
		}
	}
	report := Report{Complete: len(gaps) == 0}
	for reason := range gaps {
		report.GapReasons = append(report.GapReasons, reason)
	}
	sort.Strings(report.GapReasons)
	return batch, report, batch.Validate()
}

func canonicalMaps(row map[string]any) (attributecatalog.SpanAttributeMaps, error) {
	stringsMap, ok1 := row["attrs_string"].(map[string]string)
	numbersMap, ok2 := row["attrs_number"].(map[string]float64)
	boolMap, ok3 := row["attrs_bool"].(map[string]uint8)
	if !ok1 || !ok2 || !ok3 {
		return attributecatalog.SpanAttributeMaps{}, errors.New("observedcatalog: expected canonical typed attribute maps")
	}
	extra, ok := row["attributes_extra"].(map[string]any)
	if !ok {
		text, ok := row["attributes_extra"].(string)
		if !ok {
			return attributecatalog.SpanAttributeMaps{}, errors.New("observedcatalog: expected canonical JSON overflow")
		}
		decoder := json.NewDecoder(strings.NewReader(text))
		decoder.UseNumber()
		if err := decoder.Decode(&extra); err != nil || extra == nil {
			return attributecatalog.SpanAttributeMaps{}, errors.New("observedcatalog: invalid JSON overflow object")
		}
		var trailing any
		if decoder.Decode(&trailing) != io.EOF {
			return attributecatalog.SpanAttributeMaps{}, fmt.Errorf("observedcatalog: trailing overflow JSON")
		}
	}
	return attributecatalog.SpanAttributeMaps{Strings: copyMap(stringsMap), Numbers: copyMap(numbersMap), Booleans: copyMap(boolMap), Extra: copyMap(extra)}, nil
}

func copyMap[T any](in map[string]T) map[string]T {
	out := make(map[string]T, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

// Merge compacts observations with exact scoped identities, including JSON
// bytes in value identities. It is associative, commutative and idempotent.
func Merge(batches ...Batch) Batch {
	keys := map[KeyRow]KeyRow{}
	values := map[ValueRow]ValueRow{}
	for _, batch := range batches {
		for _, row := range batch.Keys {
			id := row
			id.FirstSeen = ""
			id.LastSeen = ""
			if prior, ok := keys[id]; ok {
				row.FirstSeen = min(row.FirstSeen, prior.FirstSeen)
				row.LastSeen = max(row.LastSeen, prior.LastSeen)
			}
			keys[id] = row
		}
		for _, row := range batch.Values {
			id := row
			id.FirstSeen = ""
			id.LastSeen = ""
			if prior, ok := values[id]; ok {
				row.FirstSeen = min(row.FirstSeen, prior.FirstSeen)
				row.LastSeen = max(row.LastSeen, prior.LastSeen)
			}
			values[id] = row
		}
	}
	out := Batch{Keys: []KeyRow{}, Values: []ValueRow{}}
	for _, row := range keys {
		out.Keys = append(out.Keys, row)
	}
	for _, row := range values {
		out.Values = append(out.Values, row)
	}
	sort.Slice(out.Keys, func(i, j int) bool {
		a, _ := json.Marshal(out.Keys[i])
		b, _ := json.Marshal(out.Keys[j])
		return string(a) < string(b)
	})
	sort.Slice(out.Values, func(i, j int) bool {
		a, _ := json.Marshal(out.Values[i])
		b, _ := json.Marshal(out.Values[j])
		return string(a) < string(b)
	})
	return out
}
