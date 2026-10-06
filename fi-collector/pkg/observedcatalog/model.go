// Package observedcatalog delivers additive attribute observations. Kafka and
// local replay may duplicate observations; readers merge first/last seen by
// min/max. Backfill repairs the canonical-commit to local-handoff crash gap.
package observedcatalog

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
	"github.com/google/uuid"
	"golang.org/x/text/cases"
)

const (
	Format         = "futureagi.observed-attributes"
	Version        = 1
	DefaultTopic   = "futureagi.observed-attributes.v1"
	DefaultGroup   = "futureagi.observed-attributes.consumer.v1"
	KeyTable       = "observed_attribute_keys"
	ValueTable     = "observed_attribute_values"
	TimeLayout     = "2006-01-02 15:04:05.000000"
	MaxRecordBytes = 512 << 10
	MaxRecordRows  = 1000
	MaxKeyBytes    = 4096
	// Eligibility is measured on original UTF-8 string bytes, not on its
	// expanded JSON or folded representation. Array strings have a lower cap.
	MaxStringValueBytes      = 16 << 10
	MaxArrayStringValueBytes = 4 << 10
	MaxValueBytes            = 6*MaxStringValueBytes + 2
	MaxSearchTextBytes       = 3 * MaxStringValueBytes
)

// Scope is authenticated externally; customer attributes cannot supply it.
type Scope struct {
	OrganizationID string `json:"organization_id"`
	WorkspaceID    string `json:"workspace_id"`
	ProjectID      string `json:"project_id"`
}

type KeyRow struct {
	Scope
	SourceKind    string `json:"source_kind"`
	AttributeKey  string `json:"attribute_key"`
	AttributeType string `json:"attribute_type"`
	KeyFolded     string `json:"key_folded"`
	FirstSeen     string `json:"first_seen"`
	LastSeen      string `json:"last_seen"`
}

type ValueRow struct {
	Scope
	SourceKind            string `json:"source_kind"`
	AttributeKey          string `json:"attribute_key"`
	AttributeType         string `json:"attribute_type"`
	ValueFingerprint      string `json:"value_fingerprint"`
	ValueJSON             string `json:"value_json"`
	ValueSearchTextFolded string `json:"value_search_text_folded"`
	FirstSeen             string `json:"first_seen"`
	LastSeen              string `json:"last_seen"`
}

// Batch is shared by extraction, backfill, publication and the ClickHouse sink.
// A batch may contain multiple authenticated scopes; every row carries its own.
type Batch struct {
	Keys   []KeyRow   `json:"keys"`
	Values []ValueRow `json:"values"`
}

type envelope struct {
	Format  string `json:"format"`
	Version int    `json:"version"`
	Batch
}

func (b Batch) Empty() bool { return len(b.Keys)+len(b.Values) == 0 }

func validateScope(s Scope) error {
	for _, value := range []string{s.OrganizationID, s.WorkspaceID, s.ProjectID} {
		id, err := uuid.Parse(value)
		if err != nil || id == uuid.Nil || id.String() != value {
			return errors.New("observedcatalog: invalid canonical scope UUID")
		}
	}
	return nil
}

func validateKey(s Scope, kind, key, typ, first, last string) error {
	if err := validateScope(s); err != nil {
		return err
	}
	if kind != attributecatalog.SourceKindCustomAttribute && kind != attributecatalog.SourceKindSystemAttribute {
		return errors.New("observedcatalog: invalid source kind")
	}
	if key == "" || len(key) > MaxKeyBytes || !utf8.ValidString(key) {
		return errors.New("observedcatalog: invalid attribute key")
	}
	switch typ {
	case "string", "number", "boolean", "array", "map", "json":
	default:
		return errors.New("observedcatalog: invalid attribute type")
	}
	f, e1 := time.Parse(TimeLayout, first)
	l, e2 := time.Parse(TimeLayout, last)
	if e1 != nil || e2 != nil || f.Format(TimeLayout) != first || l.Format(TimeLayout) != last || l.Before(f) {
		return errors.New("observedcatalog: invalid observation interval")
	}
	return nil
}

// Validate checks the row contract without imposing transport chunk limits.
func (b Batch) Validate() error {
	for _, row := range b.Keys {
		if err := validateKey(row.Scope, row.SourceKind, row.AttributeKey, row.AttributeType, row.FirstSeen, row.LastSeen); err != nil {
			return err
		}
		if row.KeyFolded != fold(row.AttributeKey) {
			return errors.New("observedcatalog: invalid folded key")
		}
	}
	for _, row := range b.Values {
		if err := validateKey(row.Scope, row.SourceKind, row.AttributeKey, row.AttributeType, row.FirstSeen, row.LastSeen); err != nil {
			return err
		}
		if len(row.ValueJSON) > MaxValueBytes || len(row.ValueSearchTextFolded) > MaxSearchTextBytes {
			return errors.New("observedcatalog: value exceeds row limit")
		}
		decoder := json.NewDecoder(strings.NewReader(row.ValueJSON))
		decoder.UseNumber()
		var value any
		if err := decoder.Decode(&value); err != nil {
			return errors.New("observedcatalog: invalid scalar JSON")
		}
		var trailing any
		if decoder.Decode(&trailing) != io.EOF {
			return errors.New("observedcatalog: trailing scalar JSON")
		}
		if text, ok := value.(string); ok && len(text) > stringValueLimit(row.AttributeType) {
			return errors.New("observedcatalog: string exceeds raw eligibility limit")
		}
		scalar, err := attributecatalog.EncodeScalar(value)
		if err != nil || scalar.ValueJSON != row.ValueJSON || scalar.Fingerprint != row.ValueFingerprint || fold(scalar.SearchText) != row.ValueSearchTextFolded {
			return errors.New("observedcatalog: noncanonical scalar or fingerprint")
		}
		if row.AttributeType != "array" && row.AttributeType != scalar.Kind {
			return errors.New("observedcatalog: scalar type mismatch")
		}
	}
	return nil
}

func fold(value string) string { return cases.Fold().String(value) }

func stringValueLimit(attributeType string) int {
	if attributeType == "array" {
		return MaxArrayStringValueBytes
	}
	return MaxStringValueBytes
}

// Encode returns the observation wire format, never a legacy catalog envelope.
func Encode(b Batch) ([]byte, error) {
	if b.Empty() || len(b.Keys)+len(b.Values) > MaxRecordRows {
		return nil, errors.New("observedcatalog: empty or oversized record")
	}
	if err := b.Validate(); err != nil {
		return nil, err
	}
	raw, err := json.Marshal(envelope{Format: Format, Version: Version, Batch: b})
	if len(raw) > MaxRecordBytes {
		return nil, errors.New("observedcatalog: record exceeds byte limit")
	}
	return raw, err
}

func Decode(raw []byte) (Batch, error) {
	if len(raw) > MaxRecordBytes {
		return Batch{}, errors.New("observedcatalog: record exceeds byte limit")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var env envelope
	if err := decoder.Decode(&env); err != nil {
		return Batch{}, errors.New("observedcatalog: invalid record JSON")
	}
	var trailing any
	if decoder.Decode(&trailing) != io.EOF || env.Format != Format || env.Version != Version {
		return Batch{}, errors.New("observedcatalog: unsupported observation record")
	}
	canonical, err := Encode(env.Batch)
	if err != nil {
		return Batch{}, err
	}
	if !bytes.Equal(canonical, raw) {
		return Batch{}, errors.New("observedcatalog: noncanonical record")
	}
	return env.Batch, nil
}

// Chunk partitions all eligible rows without truncating on total encoded bytes.
func Chunk(b Batch) ([]Batch, error) {
	if err := b.Validate(); err != nil {
		return nil, err
	}
	var result []Batch
	current := Batch{}
	// Account exactly for JSON framing and commas, avoiding repeated marshals
	// of an ever-growing batch. Empty arrays are encoded as [] consistently.
	current.Keys = []KeyRow{}
	current.Values = []ValueRow{}
	base, _ := json.Marshal(envelope{Format: Format, Version: Version, Batch: current})
	size := len(base)
	flush := func() {
		if !current.Empty() {
			result = append(result, current)
			current = Batch{Keys: []KeyRow{}, Values: []ValueRow{}}
			size = len(base)
		}
	}
	add := func(row any, key bool) error {
		raw, err := json.Marshal(row)
		if err != nil {
			return err
		}
		if len(raw)+len(base) > MaxRecordBytes {
			return fmt.Errorf("observedcatalog: individual row exceeds record limit")
		}
		comma := 0
		if (key && len(current.Keys) > 0) || (!key && len(current.Values) > 0) {
			comma = 1
		}
		if size+len(raw)+comma > MaxRecordBytes || len(current.Keys)+len(current.Values) >= MaxRecordRows {
			flush()
			comma = 0
		}
		if key {
			current.Keys = append(current.Keys, row.(KeyRow))
		} else {
			current.Values = append(current.Values, row.(ValueRow))
		}
		size += len(raw) + comma
		return nil
	}
	for _, row := range b.Keys {
		if err := add(row, true); err != nil {
			return nil, err
		}
	}
	for _, row := range b.Values {
		if err := add(row, false); err != nil {
			return nil, err
		}
	}
	flush()
	return result, nil
}
