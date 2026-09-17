package attributecatalog

import (
	"container/heap"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	AttributeTypeString  = "string"
	AttributeTypeNumber  = "number"
	AttributeTypeBoolean = "boolean"
	AttributeTypeArray   = "array"
	AttributeTypeMap     = "map"
	AttributeTypeJSON    = "json"
)

const (
	SourceKindCustomAttribute = "custom_attribute"
	SourceKindSystemAttribute = "system_attribute"
)

const (
	GapMaxKeys             = "max_keys"
	GapMaxArrayMembers     = "max_array_members"
	GapMaxEncodedBytes     = "max_encoded_bytes"
	GapInvalidAttributeKey = "invalid_attribute_key"
	GapInvalidScalar       = "invalid_scalar"
	GapInvalidBoolean      = "invalid_boolean"
)

var gapReasonOrder = [...]string{
	GapMaxKeys,
	GapMaxArrayMembers,
	GapMaxEncodedBytes,
	GapInvalidAttributeKey,
	GapInvalidScalar,
	GapInvalidBoolean,
}

// Scope is the fixed catalog identity copied to every row produced for one
// already-canonical span. The builder deliberately does not accept batches.
type Scope struct {
	ProjectID    string
	SeenAt       time.Time
	CatalogEpoch uint16
}

// SpanAttributeMaps is the typed output of adapter.Split plus its JSON
// overflow map. A caller must clear/rebuild these maps for each span before
// invoking BuildRows.
type SpanAttributeMaps struct {
	Strings  map[string]string
	Numbers  map[string]float64
	Booleans map[string]uint8
	Extra    map[string]any
}

// BuildLimits are hard per-span ceilings. Zero is a valid fail-closed limit;
// negative limits are rejected.
type BuildLimits struct {
	MaxKeys         int
	MaxArrayMembers int
	MaxEncodedBytes int
}

// KeyRow mirrors span_attribute_key_catalog's insertable columns.
type KeyRow struct {
	ProjectID     string
	SourceKind    string
	AttributeKey  string
	KeyFolded     string
	AttributeType string
	FirstSeen     time.Time
	LastSeen      time.Time
	CatalogEpoch  uint16
}

// ValueRow mirrors span_attribute_value_catalog's insertable columns.
type ValueRow struct {
	ProjectID        string
	SourceKind       string
	AttributeKey     string
	AttributeType    string
	ValueFingerprint string
	ValueJSON        string
	ValueSearchText  string
	FirstSeen        time.Time
	LastSeen         time.Time
	CatalogEpoch     uint16
}

// BuildMetadata makes every incomplete row set explicit. GapReasons has a
// stable order independent of Go map iteration, so the caller can safely turn
// any !Complete result into a coverage gap without parsing log text.
type BuildMetadata struct {
	Complete                     bool
	Truncated                    bool
	GapReasons                   []string
	CandidateKeys                int
	ValidCandidateKeys           int
	KeyRowsEmitted               int
	KeysOmitted                  int
	ValueRowsEmitted             int
	ArrayMembersTotal            int
	ArrayMembersInspected        int
	ArrayMembersOmitted          int
	NonScalarArrayMembersSkipped int
	DuplicateValuesSkipped       int
	InvalidAttributeKeys         int
	InvalidScalarValues          int
	InvalidBooleanValues         int
	EncodedBytes                 int
}

// BuildResult is bounded by MaxKeys key rows and at most
// MaxKeys+MaxArrayMembers value rows. No output references a batch-wide
// attribute Cartesian product.
type BuildResult struct {
	KeyRows   []KeyRow
	ValueRows []ValueRow
	Metadata  BuildMetadata
}

type attributeCandidate struct {
	key           string
	attributeType string
	value         any
	array         []any
}

type maxCandidateHeap []attributeCandidate

func (h maxCandidateHeap) Len() int { return len(h) }

// container/heap is a min-heap. Reverse our canonical comparison so the
// largest retained candidate is at index zero and can be replaced in O(log K).
func (h maxCandidateHeap) Less(i, j int) bool {
	return candidateLess(h[j], h[i])
}

func (h maxCandidateHeap) Swap(i, j int) { h[i], h[j] = h[j], h[i] }

func (h *maxCandidateHeap) Push(value any) {
	*h = append(*h, value.(attributeCandidate))
}

func (h *maxCandidateHeap) Pop() any {
	old := *h
	last := len(old) - 1
	value := old[last]
	*h = old[:last]
	return value
}

type valueIdentity struct {
	key           string
	attributeType string
	fingerprint   string
}

// BuildRows constructs catalog rows for exactly one span. Key selection is a
// deterministic, bounded top-K over (attribute_key, attribute_type); it never
// copies all input keys merely to sort them. Arrays retain source order and
// share one global member-inspection budget across the selected keys.
//
// EncodedBytes counts the dynamic UTF-8 fields inserted by these rows:
//
//   - key row: key + folded key + attribute type
//   - value row: key + attribute type + fingerprint + value JSON + search text
//
// Fixed-width UUID/time/epoch columns are excluded. A row is atomic: if its
// dynamic payload does not fit, it is not emitted and GapMaxEncodedBytes is
// reported. Key/type discovery is completed before value retention, so an
// oversized value cannot hide a later attribute key that still fits.
func BuildRows(
	scope Scope,
	attrs SpanAttributeMaps,
	limits BuildLimits,
) (BuildResult, error) {
	return BuildRowsForSource(scope, attrs, limits, SourceKindCustomAttribute)
}

// BuildRowsForSource keeps custom and code-owned system properties in the
// same catalog tables without allowing equal names to share an identity.
func BuildRowsForSource(
	scope Scope,
	attrs SpanAttributeMaps,
	limits BuildLimits,
	sourceKind string,
) (BuildResult, error) {
	if limits.MaxKeys < 0 || limits.MaxArrayMembers < 0 || limits.MaxEncodedBytes < 0 {
		return BuildResult{}, fmt.Errorf("catalog build limits must be non-negative")
	}
	if sourceKind != SourceKindCustomAttribute && sourceKind != SourceKindSystemAttribute {
		return BuildResult{}, fmt.Errorf("unsupported catalog source kind")
	}

	selected, validKeys, invalidKeys := selectCandidates(attrs, limits.MaxKeys)
	metadata := BuildMetadata{
		CandidateKeys:        len(attrs.Strings) + len(attrs.Numbers) + len(attrs.Booleans) + len(attrs.Extra),
		ValidCandidateKeys:   validKeys,
		InvalidAttributeKeys: invalidKeys,
	}
	reasons := make(map[string]struct{}, len(gapReasonOrder))
	if validKeys > limits.MaxKeys {
		reasons[GapMaxKeys] = struct{}{}
	}
	if invalidKeys > 0 {
		reasons[GapInvalidAttributeKey] = struct{}{}
	}
	for _, candidate := range selected {
		if candidate.attributeType == AttributeTypeArray {
			metadata.ArrayMembersTotal += len(candidate.array)
		}
	}
	if metadata.ArrayMembersTotal > limits.MaxArrayMembers {
		reasons[GapMaxArrayMembers] = struct{}{}
	}

	result := BuildResult{KeyRows: make([]KeyRow, 0, len(selected))}
	valueCandidates := make([]attributeCandidate, 0, len(selected))
	seenValues := make(map[valueIdentity]struct{})

	// Key/type discovery has priority over optional retained values. Keeping
	// this as a separate bounded pass prevents an early large value from
	// consuming the budget needed to describe later properties.
	for _, candidate := range selected {
		remaining := limits.MaxEncodedBytes - metadata.EncodedBytes
		keyCost, fits := keyRowEncodedSize(candidate.key, candidate.attributeType, remaining)
		if !fits {
			reasons[GapMaxEncodedBytes] = struct{}{}
			continue
		}

		result.KeyRows = append(result.KeyRows, KeyRow{
			ProjectID:     scope.ProjectID,
			SourceKind:    sourceKind,
			AttributeKey:  candidate.key,
			KeyFolded:     foldAttributeKey(candidate.key),
			AttributeType: candidate.attributeType,
			FirstSeen:     scope.SeenAt,
			LastSeen:      scope.SeenAt,
			CatalogEpoch:  scope.CatalogEpoch,
		})
		metadata.EncodedBytes += keyCost
		valueCandidates = append(valueCandidates, candidate)
	}

	for _, candidate := range valueCandidates {
		switch candidate.attributeType {
		case AttributeTypeMap, AttributeTypeJSON:
			// Intentional key-only shapes, not a coverage gap.
			continue
		case AttributeTypeBoolean:
			encodedBoolean, ok := candidate.value.(uint8)
			if !ok || encodedBoolean > 1 {
				metadata.InvalidBooleanValues++
				reasons[GapInvalidBoolean] = struct{}{}
				continue
			}
			status := appendScalarValue(
				&result,
				&metadata,
				seenValues,
				scope,
				sourceKind,
				candidate,
				encodedBoolean == 1,
				limits.MaxEncodedBytes,
			)
			switch status {
			case scalarInvalid:
				metadata.InvalidScalarValues++
				reasons[GapInvalidScalar] = struct{}{}
			case scalarByteLimit:
				reasons[GapMaxEncodedBytes] = struct{}{}
			}
		case AttributeTypeArray:
			remainingMembers := limits.MaxArrayMembers - metadata.ArrayMembersInspected
			inspectCount := min(len(candidate.array), max(remainingMembers, 0))
			for _, member := range candidate.array[:inspectCount] {
				metadata.ArrayMembersInspected++
				if !isSelectableScalar(member) {
					metadata.NonScalarArrayMembersSkipped++
					continue
				}
				status := appendScalarValue(
					&result,
					&metadata,
					seenValues,
					scope,
					sourceKind,
					candidate,
					member,
					limits.MaxEncodedBytes,
				)
				switch status {
				case scalarInvalid:
					metadata.InvalidScalarValues++
					reasons[GapInvalidScalar] = struct{}{}
				case scalarByteLimit:
					reasons[GapMaxEncodedBytes] = struct{}{}
				}
			}
		default:
			status := appendScalarValue(
				&result,
				&metadata,
				seenValues,
				scope,
				sourceKind,
				candidate,
				candidate.value,
				limits.MaxEncodedBytes,
			)
			switch status {
			case scalarInvalid:
				metadata.InvalidScalarValues++
				reasons[GapInvalidScalar] = struct{}{}
			case scalarByteLimit:
				reasons[GapMaxEncodedBytes] = struct{}{}
			}
		}
	}

	metadata.KeyRowsEmitted = len(result.KeyRows)
	metadata.KeysOmitted = metadata.CandidateKeys - metadata.KeyRowsEmitted
	metadata.ValueRowsEmitted = len(result.ValueRows)
	metadata.ArrayMembersOmitted = metadata.ArrayMembersTotal - metadata.ArrayMembersInspected
	metadata.GapReasons = orderedGapReasons(reasons)
	metadata.Complete = len(metadata.GapReasons) == 0
	metadata.Truncated = hasTruncationReason(reasons)
	result.Metadata = metadata
	return result, nil
}

func selectCandidates(attrs SpanAttributeMaps, maxKeys int) ([]attributeCandidate, int, int) {
	// Grow from observed retained candidates only. A huge caller limit with a
	// tiny span must not trigger a huge allocation before any work is done.
	selected := maxCandidateHeap{}
	heap.Init(&selected)
	validKeys := 0
	invalidKeys := 0

	consider := func(candidate attributeCandidate) {
		if !utf8.ValidString(candidate.key) {
			invalidKeys++
			return
		}
		validKeys++
		if maxKeys == 0 {
			return
		}
		if len(selected) < maxKeys {
			heap.Push(&selected, candidate)
			return
		}
		if candidateLess(candidate, selected[0]) {
			selected[0] = candidate
			heap.Fix(&selected, 0)
		}
	}

	for key, value := range attrs.Strings {
		consider(attributeCandidate{key: key, attributeType: AttributeTypeString, value: value})
	}
	for key, value := range attrs.Numbers {
		consider(attributeCandidate{key: key, attributeType: AttributeTypeNumber, value: value})
	}
	for key, value := range attrs.Booleans {
		consider(attributeCandidate{key: key, attributeType: AttributeTypeBoolean, value: value})
	}
	for key, value := range attrs.Extra {
		switch typed := value.(type) {
		case []any:
			consider(attributeCandidate{key: key, attributeType: AttributeTypeArray, array: typed})
		case map[string]any:
			consider(attributeCandidate{key: key, attributeType: AttributeTypeMap})
		default:
			consider(attributeCandidate{key: key, attributeType: AttributeTypeJSON})
		}
	}

	sort.Slice(selected, func(i, j int) bool {
		return candidateLess(selected[i], selected[j])
	})
	return selected, validKeys, invalidKeys
}

func candidateLess(left, right attributeCandidate) bool {
	if left.key != right.key {
		return left.key < right.key
	}
	return attributeTypeRank(left.attributeType) < attributeTypeRank(right.attributeType)
}

func attributeTypeRank(attributeType string) int {
	switch attributeType {
	case AttributeTypeString:
		return 1
	case AttributeTypeNumber:
		return 2
	case AttributeTypeBoolean:
		return 3
	case AttributeTypeArray:
		return 4
	case AttributeTypeMap:
		return 5
	default:
		return 6
	}
}

type scalarAppendStatus uint8

const (
	scalarAppended scalarAppendStatus = iota
	scalarDuplicate
	scalarInvalid
	scalarByteLimit
)

func appendScalarValue(
	result *BuildResult,
	metadata *BuildMetadata,
	seen map[valueIdentity]struct{},
	scope Scope,
	sourceKind string,
	candidate attributeCandidate,
	value any,
	maxEncodedBytes int,
) scalarAppendStatus {
	encoded, cost, fits, err := encodeScalarForRow(
		candidate.key,
		candidate.attributeType,
		value,
		maxEncodedBytes,
	)
	if err != nil {
		return scalarInvalid
	}
	if !fits {
		return scalarByteLimit
	}
	identity := valueIdentity{
		key:           candidate.key,
		attributeType: candidate.attributeType,
		fingerprint:   encoded.Fingerprint,
	}
	if _, duplicate := seen[identity]; duplicate {
		metadata.DuplicateValuesSkipped++
		return scalarDuplicate
	}
	if cost > maxEncodedBytes-metadata.EncodedBytes {
		return scalarByteLimit
	}

	seen[identity] = struct{}{}
	result.ValueRows = append(result.ValueRows, ValueRow{
		ProjectID:        scope.ProjectID,
		SourceKind:       sourceKind,
		AttributeKey:     candidate.key,
		AttributeType:    candidate.attributeType,
		ValueFingerprint: encoded.Fingerprint,
		ValueJSON:        encoded.ValueJSON,
		ValueSearchText:  encoded.SearchText,
		FirstSeen:        scope.SeenAt,
		LastSeen:         scope.SeenAt,
		CatalogEpoch:     scope.CatalogEpoch,
	})
	metadata.EncodedBytes += cost
	return scalarAppended
}

// encodeScalarForRow first proves the row could fit in the total byte budget.
// This avoids materializing canonical JSON proportional to an attacker-sized
// string. It still encodes a duplicate against the total (rather than the
// remaining) budget so dedupe cannot create a false truncation.
func encodeScalarForRow(
	key string,
	attributeType string,
	value any,
	maxEncodedBytes int,
) (Scalar, int, bool, error) {
	base := len(key) + len(attributeType) + 64
	if base > maxEncodedBytes {
		return Scalar{}, 0, false, nil
	}
	if text, ok := value.(string); ok {
		if !utf8.ValidString(text) {
			return Scalar{}, 0, false, fmt.Errorf("catalog strings must be valid UTF-8")
		}
		jsonBytes, fits := canonicalJSONStringSize(text, maxEncodedBytes-base-len(text))
		if len(text) > maxEncodedBytes-base || !fits {
			return Scalar{}, 0, false, nil
		}
		cost := base + len(text) + jsonBytes
		encoded, err := EncodeScalar(text)
		return encoded, cost, true, err
	}

	encoded, err := EncodeScalar(value)
	if err != nil {
		return Scalar{}, 0, false, err
	}
	cost := base + len(encoded.ValueJSON) + len(encoded.SearchText)
	if cost > maxEncodedBytes {
		return Scalar{}, 0, false, nil
	}
	return encoded, cost, true, nil
}

func canonicalJSONStringSize(value string, remaining int) (int, bool) {
	if remaining < 2 {
		return 0, false
	}
	size := 2
	for _, char := range value {
		increment := utf8.RuneLen(char)
		switch char {
		case '"', '\\', '\b', '\f', '\n', '\r', '\t':
			increment = 2
		default:
			if char < 0x20 {
				increment = 6
			}
		}
		if increment > remaining-size {
			return 0, false
		}
		size += increment
	}
	return size, true
}

func keyRowEncodedSize(key, attributeType string, remaining int) (int, bool) {
	if len(attributeType) > remaining {
		return 0, false
	}
	remaining -= len(attributeType)
	if len(key) > remaining/2 {
		return 0, false
	}
	return len(attributeType) + 2*len(key), true
}

// foldAttributeKey intentionally folds ASCII only. This keeps Go/Python byte
// parity and leaves valid non-ASCII UTF-8 intact instead of depending on
// runtime-specific Unicode expansion tables.
func foldAttributeKey(key string) string {
	firstUpper := -1
	for index := 0; index < len(key); index++ {
		if key[index] >= 'A' && key[index] <= 'Z' {
			firstUpper = index
			break
		}
	}
	if firstUpper < 0 {
		return key
	}
	var folded strings.Builder
	folded.Grow(len(key))
	folded.WriteString(key[:firstUpper])
	for index := firstUpper; index < len(key); index++ {
		char := key[index]
		if char >= 'A' && char <= 'Z' {
			char += 'a' - 'A'
		}
		folded.WriteByte(char)
	}
	return folded.String()
}

func isSelectableScalar(value any) bool {
	switch value.(type) {
	case bool, string,
		json.Number,
		int, int8, int16, int32, int64,
		uint, uint8, uint16, uint32, uint64,
		float32, float64:
		return true
	default:
		return false
	}
}

func orderedGapReasons(reasons map[string]struct{}) []string {
	ordered := make([]string, 0, len(reasons))
	for _, reason := range gapReasonOrder {
		if _, present := reasons[reason]; present {
			ordered = append(ordered, reason)
		}
	}
	return ordered
}

func hasTruncationReason(reasons map[string]struct{}) bool {
	for _, reason := range []string{GapMaxKeys, GapMaxArrayMembers, GapMaxEncodedBytes} {
		if _, present := reasons[reason]; present {
			return true
		}
	}
	return false
}
