// Package propertycatalog defines the generic, bounded wire and delivery
// contract for FutureAGI's unified property catalog. It deliberately does not
// activate a reader, create a table, or connect to Kafka/ClickHouse on import.
package propertycatalog

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/google/uuid"
	"golang.org/x/text/cases"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
)

const (
	EnvelopeFormat  = "futureagi.property-catalog-envelope"
	EnvelopeVersion = uint16(1)

	MaxRecordBytes           = 768 << 10
	MaxChunks                = 128
	MaxChunkBytes            = 512 << 10
	MaxRowsPerChunk          = 10_000
	MaxRowsPerEnvelope       = 1_000_000
	MaxGapReasons            = 64
	MaxGapReasonBytes        = 128
	MaxDefinitionJSONBytes   = 32 << 10
	MaxValueJSONBytes        = 32 << 10
	MaxValueSearchTextBytes  = 16 << 10
	MaxSourceTokens          = 64
	MaxSourceTokenBytes      = 4096
	MaxPropertyIdentityBytes = 4096
	maxGeneralStringBytes    = 4096
	dateTime64Layout         = "2006-01-02 15:04:05.000000"
	zeroSHA256               = "0000000000000000000000000000000000000000000000000000000000000000"
)

// ZeroSHA256 is the only valid previous payload digest for sequence one.
const ZeroSHA256 = zeroSHA256

// Table is closed over the two new catalog data tables. Control-plane tables
// are written by explicit methods and can never be selected by an envelope.
type Table string

const (
	DefinitionTable     Table = "property_definition_catalog"
	AttributeValueTable Table = "span_attribute_value_catalog"
)

type SourceAdapter string

const (
	AdapterSystemManifest       SourceAdapter = "system_manifest"
	AdapterSpanAttribute        SourceAdapter = "span_attribute"
	AdapterEvalTemplate         SourceAdapter = "eval_template"
	AdapterEvalConfig           SourceAdapter = "eval_config"
	AdapterSimulationEvalConfig SourceAdapter = "simulation_eval_config"
	AdapterAnnotationLabel      SourceAdapter = "annotation_label"
	AdapterDatasetColumn        SourceAdapter = "dataset_column"
)

type PropertyKind string

const (
	KindSystemAttribute PropertyKind = "system_attribute"
	KindCustomAttribute PropertyKind = "custom_attribute"
	KindEvalTemplate    PropertyKind = "eval_template"
	KindEvalConfig      PropertyKind = "eval_config"
	KindAnnotation      PropertyKind = "annotation"
	KindDatasetColumn   PropertyKind = "dataset_column"
)

type VisibilityScope string

const (
	VisibilityAlways          VisibilityScope = "always"
	VisibilityWorkspace       VisibilityScope = "workspace_default"
	VisibilityProject         VisibilityScope = "project"
	VisibilityAgentDefinition VisibilityScope = "agent_definition"
	VisibilityDataset         VisibilityScope = "dataset"
)

type Outcome string

const (
	OutcomeCommitted Outcome = "committed"
	OutcomeGap       Outcome = "gap"
)

// DefinitionRow is one immutable definition-to-visibility binding version.
// All timestamps use ClickHouse DateTime64(6, UTC)'s canonical text shape.
type DefinitionRow struct {
	OrganizationID      string          `json:"organization_id"`
	WorkspaceID         string          `json:"workspace_id"`
	CatalogEpoch        uint16          `json:"catalog_epoch"`
	CatalogRevision     uint64          `json:"catalog_revision"`
	BuildToken          string          `json:"build_token"`
	ProjectionVersion   uint16          `json:"projection_version"`
	BindingID           string          `json:"binding_id"`
	VisibilityScope     VisibilityScope `json:"visibility_scope"`
	VisibilityID        string          `json:"visibility_id"`
	SourceAdapter       SourceAdapter   `json:"source_adapter"`
	SourceEntityID      string          `json:"source_entity_id"`
	SourceVersion       uint64          `json:"source_version"`
	SourceFingerprint   string          `json:"source_fingerprint"`
	ProducerStreamID    string          `json:"producer_stream_id"`
	ProducerSequence    uint64          `json:"producer_sequence"`
	PropertyID          string          `json:"property_id"`
	PropertyKind        PropertyKind    `json:"property_kind"`
	Category            string          `json:"category"`
	CategoryRank        uint8           `json:"category_rank"`
	SourceRank          uint16          `json:"source_rank"`
	DefinitionSource    string          `json:"definition_source"`
	PrimarySource       string          `json:"primary_source"`
	PrimarySourceFolded string          `json:"primary_source_folded"`
	SourceTokens        []string        `json:"source_tokens"`
	ValueAdapter        string          `json:"value_adapter"`
	Name                string          `json:"name"`
	DisplayName         string          `json:"display_name"`
	SortNameFolded      string          `json:"sort_name_folded"`
	SearchTextFolded    string          `json:"search_text_folded"`
	Role                string          `json:"role"`
	DefinitionJSON      string          `json:"definition_json"`
	DefinitionSHA256    string          `json:"definition_sha256"`
	FirstSeen           *string         `json:"first_seen"`
	LastSeen            *string         `json:"last_seen"`
	IsDeleted           uint8           `json:"is_deleted"`
	DeletedAt           *string         `json:"deleted_at"`
	StateSHA256         string          `json:"state_sha256"`
	EmittedAt           string          `json:"emitted_at"`
}

// AttributeValueRow contains a native, observed custom/system span-attribute
// value. It is not another definition store.
type AttributeValueRow struct {
	OrganizationID        string       `json:"organization_id"`
	WorkspaceID           string       `json:"workspace_id"`
	ProjectID             string       `json:"project_id"`
	CatalogEpoch          uint16       `json:"catalog_epoch"`
	CatalogRevision       uint64       `json:"catalog_revision"`
	BuildToken            string       `json:"build_token"`
	SourceKind            PropertyKind `json:"source_kind"`
	AttributeKey          string       `json:"attribute_key"`
	AttributeType         string       `json:"attribute_type"`
	ValueFingerprint      string       `json:"value_fingerprint"`
	ValueJSON             string       `json:"value_json"`
	ValueSearchTextFolded string       `json:"value_search_text_folded"`
	FirstSeen             string       `json:"first_seen"`
	LastSeen              string       `json:"last_seen"`
}

type Scope struct {
	OrganizationID    string
	WorkspaceID       string
	CatalogEpoch      uint16
	CatalogRevision   uint64
	BuildToken        string
	ProjectionVersion uint16
	SourceAdapter     SourceAdapter
	SourceVersion     uint64
	SourceFingerprint string
	ProducerStreamID  string
	Sequence          uint64
}

func validateDefinition(row DefinitionRow, scope Scope) error {
	if err := validateScopeMatch(row.OrganizationID, row.WorkspaceID, row.CatalogEpoch,
		row.CatalogRevision, row.BuildToken, row.SourceAdapter, row.ProducerStreamID, row.ProducerSequence, scope); err != nil {
		return err
	}
	if row.ProjectionVersion != scope.ProjectionVersion {
		return errors.New("definition projection version does not match envelope")
	}
	if !isLowerSHA256(row.BindingID) || !isLowerSHA256(row.DefinitionSHA256) ||
		!isLowerSHA256(row.SourceFingerprint) || !isLowerSHA256(row.StateSHA256) {
		return errors.New("definition contains an invalid lowercase SHA-256 identity")
	}
	if err := validateVisibility(row.VisibilityScope, row.VisibilityID, row.WorkspaceID); err != nil {
		return err
	}
	if !validPropertyKind(row.PropertyKind) {
		return errors.New("definition has unsupported property kind")
	}
	for name, value := range map[string]string{
		"property_id": row.PropertyID, "category": row.Category,
		"source_entity_id":  row.SourceEntityID,
		"definition_source": row.DefinitionSource, "value_adapter": row.ValueAdapter,
		"name": row.Name, "display_name": row.DisplayName, "sort_name_folded": row.SortNameFolded,
		"search_text_folded": row.SearchTextFolded,
	} {
		if err := validateText(name, value, true, maxGeneralStringBytes); err != nil {
			return err
		}
	}
	for name, value := range map[string]string{
		"primary_source": row.PrimarySource, "primary_source_folded": row.PrimarySourceFolded,
	} {
		if err := validateText(name, value, false, maxGeneralStringBytes); err != nil {
			return err
		}
	}
	if len(row.PropertyID) > MaxPropertyIdentityBytes {
		return errors.New("definition property_id exceeds byte limit")
	}
	if row.SourceVersion == 0 || row.SourceVersion != scope.SourceVersion ||
		row.SourceFingerprint != scope.SourceFingerprint {
		return errors.New("definition source fence does not match envelope")
	}
	switch row.Category {
	case "system_metric", "eval_metric", "annotation_metric", "custom_attribute", "custom_column":
	default:
		return errors.New("definition has unsupported category")
	}
	if row.Role != "metric" && row.Role != "dimension" {
		return errors.New("definition has unsupported role")
	}
	if row.SourceTokens == nil || len(row.SourceTokens) > MaxSourceTokens {
		return errors.New("definition source_tokens is nil or exceeds limit")
	}
	if !slices.IsSorted(row.SourceTokens) {
		return errors.New("definition source_tokens must be sorted")
	}
	for index, token := range row.SourceTokens {
		if err := validateText(fmt.Sprintf("source_tokens[%d]", index), token, true, MaxSourceTokenBytes); err != nil {
			return err
		}
		if index > 0 && token == row.SourceTokens[index-1] {
			return errors.New("definition source_tokens contains a duplicate")
		}
	}
	definition, err := decodeDefinitionJSON(row.DefinitionJSON)
	if err != nil {
		return err
	}
	digest := sha256.Sum256([]byte(row.DefinitionJSON))
	if hex.EncodeToString(digest[:]) != row.DefinitionSHA256 {
		return errors.New("definition_json digest mismatch")
	}
	if err := validateDefinitionProjection(row, definition); err != nil {
		return err
	}
	if row.IsDeleted > 1 || (row.IsDeleted == 1) != (row.DeletedAt != nil) {
		return errors.New("definition tombstone and deleted_at disagree")
	}
	if (row.FirstSeen == nil) != (row.LastSeen == nil) {
		return errors.New("definition first_seen and last_seen nullability disagree")
	}
	if row.FirstSeen != nil {
		if err := validateDateTime64("first_seen", *row.FirstSeen); err != nil {
			return err
		}
		if err := validateDateTime64("last_seen", *row.LastSeen); err != nil {
			return err
		}
		first, _ := time.Parse(dateTime64Layout, *row.FirstSeen)
		last, _ := time.Parse(dateTime64Layout, *row.LastSeen)
		if last.Before(first) {
			return errors.New("definition last_seen precedes first_seen")
		}
	}
	if row.DeletedAt != nil {
		if err := validateDateTime64("deleted_at", *row.DeletedAt); err != nil {
			return err
		}
	}
	if err := validateDateTime64("emitted_at", row.EmittedAt); err != nil {
		return err
	}
	expectedBinding := bindingIDForRow(row)
	if row.BindingID != expectedBinding {
		return errors.New("definition binding_id does not match binding fields")
	}
	expectedState, err := stateSHA256ForRow(row)
	if err != nil {
		return err
	}
	if row.StateSHA256 != expectedState {
		return errors.New("definition state_sha256 does not match binding state")
	}
	return nil
}

type canonicalDefinitionPayload struct {
	Category         string         `json:"category"`
	CategoryRank     uint8          `json:"category_rank"`
	DefinitionSource string         `json:"definition_source"`
	Details          map[string]any `json:"details"`
	DisplayName      string         `json:"display_name"`
	Name             string         `json:"name"`
	OutputType       string         `json:"output_type"`
	PrimarySource    string         `json:"primary_source"`
	PropertyID       string         `json:"property_id"`
	PropertyKind     PropertyKind   `json:"property_kind"`
	Role             string         `json:"role"`
	SourceRank       uint16         `json:"source_rank"`
	SourceTokens     []string       `json:"source_tokens"`
	ValueAdapter     string         `json:"value_adapter"`
	ValueType        string         `json:"value_type"`
}

var canonicalDefinitionFields = map[string]struct{}{
	"category": {}, "category_rank": {}, "definition_source": {}, "details": {},
	"display_name": {}, "name": {}, "output_type": {}, "primary_source": {},
	"property_id": {}, "property_kind": {}, "role": {}, "source_rank": {},
	"source_tokens": {}, "value_adapter": {}, "value_type": {},
}

func decodeDefinitionJSON(raw string) (canonicalDefinitionPayload, error) {
	if err := validateCanonicalJSON("definition_json", raw, MaxDefinitionJSONBytes, false); err != nil {
		return canonicalDefinitionPayload{}, err
	}
	var object map[string]any
	generic := json.NewDecoder(strings.NewReader(raw))
	generic.UseNumber()
	if err := generic.Decode(&object); err != nil {
		return canonicalDefinitionPayload{}, err
	}
	if len(object) != len(canonicalDefinitionFields) {
		return canonicalDefinitionPayload{}, errors.New("definition_json does not have the exact 15-key shape")
	}
	for field := range object {
		if _, ok := canonicalDefinitionFields[field]; !ok {
			return canonicalDefinitionPayload{}, fmt.Errorf("definition_json contains unsupported field %q", field)
		}
	}
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	var definition canonicalDefinitionPayload
	if err := decoder.Decode(&definition); err != nil {
		return canonicalDefinitionPayload{}, fmt.Errorf("propertycatalog: decode definition_json shape: %w", err)
	}
	if definition.Details == nil || definition.SourceTokens == nil {
		return canonicalDefinitionPayload{}, errors.New("definition_json details/source_tokens must not be null")
	}
	if err := validateDefinitionDetails(definition.Details); err != nil {
		return canonicalDefinitionPayload{}, err
	}
	for name, value := range map[string]string{
		"definition_json.definition_source": definition.DefinitionSource,
		"definition_json.display_name":      definition.DisplayName,
		"definition_json.name":              definition.Name,
		"definition_json.property_id":       definition.PropertyID,
		"definition_json.value_adapter":     definition.ValueAdapter,
		"definition_json.value_type":        definition.ValueType,
	} {
		if err := validateText(name, value, true, maxGeneralStringBytes); err != nil {
			return canonicalDefinitionPayload{}, err
		}
	}
	for name, value := range map[string]string{
		"definition_json.output_type":    definition.OutputType,
		"definition_json.primary_source": definition.PrimarySource,
	} {
		if err := validateText(name, value, false, maxGeneralStringBytes); err != nil {
			return canonicalDefinitionPayload{}, err
		}
	}
	return definition, nil
}

func validateDefinitionDetails(details map[string]any) error {
	_, hasAttributeTypes := details["attribute_types"]
	_, hasAttributeTypesExact := details["attribute_types_exact"]
	if hasAttributeTypes != hasAttributeTypesExact {
		return errors.New("definition_json details.attribute_types and attribute_types_exact must be present together")
	}
	for key, value := range details {
		switch key {
		case "unit", "data_type":
			text, ok := value.(string)
			if !ok {
				return fmt.Errorf("definition_json details.%s must be a string", key)
			}
			if err := validateText("definition_json details."+key, text, false, maxGeneralStringBytes); err != nil {
				return err
			}
		case "eval_template_id":
			text, ok := value.(string)
			if !ok {
				return errors.New("definition_json details.eval_template_id must be a UUID string")
			}
			if err := validateCanonicalUUID("definition_json details.eval_template", text); err != nil {
				return err
			}
		case "choices", "choice_options":
			if _, ok := value.([]any); !ok {
				return fmt.Errorf("definition_json details.%s must be an array", key)
			}
		case "allowed_aggregations":
			items, ok := value.([]any)
			if !ok {
				return errors.New("definition_json details.allowed_aggregations must be an array")
			}
			for index, item := range items {
				text, ok := item.(string)
				if !ok {
					return fmt.Errorf("definition_json details.allowed_aggregations[%d] must be a string", index)
				}
				if err := validateText(
					fmt.Sprintf("definition_json details.allowed_aggregations[%d]", index),
					text, true, maxGeneralStringBytes,
				); err != nil {
					return err
				}
			}
		case "attribute_types":
			items, ok := value.([]any)
			if !ok || len(items) == 0 || len(items) > 6 {
				return errors.New("definition_json details.attribute_types must be a non-empty bounded array")
			}
			previous := ""
			for index, item := range items {
				text, ok := item.(string)
				if !ok {
					return fmt.Errorf("definition_json details.attribute_types[%d] must be a string", index)
				}
				switch text {
				case attributecatalog.AttributeTypeString, attributecatalog.AttributeTypeNumber,
					attributecatalog.AttributeTypeBoolean, attributecatalog.AttributeTypeArray,
					attributecatalog.AttributeTypeMap, attributecatalog.AttributeTypeJSON:
				default:
					return fmt.Errorf("definition_json details.attribute_types[%d] is unsupported", index)
				}
				if index > 0 && text <= previous {
					return errors.New("definition_json details.attribute_types must be strictly sorted")
				}
				previous = text
			}
		case "attribute_types_exact":
			if _, ok := value.(bool); !ok {
				return errors.New("definition_json details.attribute_types_exact must be a boolean")
			}
		default:
			return fmt.Errorf("definition_json details contains unsupported field %q", key)
		}
	}
	return nil
}

func validateDefinitionProjection(row DefinitionRow, definition canonicalDefinitionPayload) error {
	if definition.PropertyID != row.PropertyID || definition.PropertyKind != row.PropertyKind ||
		definition.Category != row.Category || definition.CategoryRank != row.CategoryRank ||
		definition.SourceRank != row.SourceRank || definition.DefinitionSource != row.DefinitionSource ||
		definition.PrimarySource != row.PrimarySource || !slices.Equal(definition.SourceTokens, row.SourceTokens) ||
		definition.ValueAdapter != row.ValueAdapter || definition.Name != row.Name ||
		definition.DisplayName != row.DisplayName || definition.Role != row.Role {
		return errors.New("definition_json does not match projected row fields")
	}
	if !validPropertyKind(definition.PropertyKind) {
		return errors.New("definition_json has unsupported property kind")
	}
	if _, hasAttributeTypes := definition.Details["attribute_types"]; hasAttributeTypes &&
		definition.PropertyKind != KindCustomAttribute {
		return errors.New("definition_json attribute type union is only valid for custom attributes")
	}
	switch definition.Category {
	case "system_metric", "eval_metric", "annotation_metric", "custom_attribute", "custom_column":
	default:
		return errors.New("definition_json has unsupported category")
	}
	if definition.Role != "metric" && definition.Role != "dimension" {
		return errors.New("definition_json has unsupported role")
	}
	if row.PrimarySourceFolded != foldPropertyText(row.PrimarySource) ||
		row.SortNameFolded != foldPropertyText(row.Name) {
		return errors.New("definition folded sort fields do not match source fields")
	}
	components := append([]string{row.Name, row.DisplayName, row.PrimarySource, row.DefinitionSource}, row.SourceTokens...)
	search := make([]string, 0, len(components))
	seen := make(map[string]struct{}, len(components))
	for _, component := range components {
		if component == "" {
			continue
		}
		folded := foldPropertyText(component)
		if _, exists := seen[folded]; exists {
			continue
		}
		seen[folded] = struct{}{}
		search = append(search, folded)
	}
	if row.SearchTextFolded != strings.Join(search, " ") {
		return errors.New("definition search_text_folded does not match source fields")
	}
	return nil
}

func foldPropertyText(value string) string {
	return cases.Fold().String(value)
}

func bindingIDForRow(row DefinitionRow) string {
	return framedSHA256(
		"futureagi.property-catalog.binding.v1",
		row.OrganizationID, row.WorkspaceID, string(row.VisibilityScope), row.VisibilityID,
		row.PropertyID, string(row.SourceAdapter),
	)
}

func stateSHA256ForRow(row DefinitionRow) (string, error) {
	deletedAt, err := pythonTimestampComponent(row.DeletedAt)
	if err != nil {
		return "", err
	}
	firstSeen, err := pythonTimestampComponent(row.FirstSeen)
	if err != nil {
		return "", err
	}
	lastSeen, err := pythonTimestampComponent(row.LastSeen)
	if err != nil {
		return "", err
	}
	return framedSHA256(
		"futureagi.property-catalog.binding-state.v1",
		row.BindingID, row.DefinitionSHA256, row.SourceEntityID, row.SourceVersion,
		row.SourceFingerprint, row.IsDeleted == 1, deletedAt, firstSeen, lastSeen,
	), nil
}

func pythonTimestampComponent(value *string) (any, error) {
	if value == nil {
		return nil, nil
	}
	parsed, err := time.Parse(dateTime64Layout, *value)
	if err != nil || parsed.Format(dateTime64Layout) != *value {
		return nil, errors.New("propertycatalog: state timestamp is not canonical DateTime64(6)")
	}
	return parsed.UTC().Format("2006-01-02T15:04:05.000000+00:00"), nil
}

func framedSHA256(domain string, components ...any) string {
	digest := sha256.New()
	var domainLength [4]byte
	binary.BigEndian.PutUint32(domainLength[:], uint32(len(domain)))
	digest.Write(domainLength[:])
	digest.Write([]byte(domain))
	for _, component := range components {
		var encoded []byte
		switch value := component.(type) {
		case nil:
			encoded = []byte("<null>")
		case string:
			encoded = []byte(value)
		case uint64:
			encoded = []byte(strconv.FormatUint(value, 10))
		case bool:
			encoded = []byte(strconv.FormatBool(value))
		default:
			panic(fmt.Sprintf("propertycatalog: unsupported framed hash component %T", component))
		}
		var length [8]byte
		binary.BigEndian.PutUint64(length[:], uint64(len(encoded)))
		digest.Write(length[:])
		digest.Write(encoded)
	}
	return hex.EncodeToString(digest.Sum(nil))
}

func validateAttributeValue(row AttributeValueRow, scope Scope) error {
	if row.OrganizationID != scope.OrganizationID || row.WorkspaceID != scope.WorkspaceID {
		return errors.New("native attribute row tenant does not match envelope")
	}
	if row.CatalogEpoch != scope.CatalogEpoch {
		return errors.New("native attribute row epoch does not match envelope")
	}
	if row.CatalogRevision == 0 || row.CatalogRevision != scope.CatalogRevision {
		return errors.New("native attribute row revision does not match envelope")
	}
	if row.BuildToken != scope.BuildToken {
		return errors.New("native attribute row build token does not match envelope")
	}
	if scope.SourceAdapter != AdapterSpanAttribute {
		return errors.New("native attribute values require the span_attribute adapter")
	}
	if err := validateCanonicalUUID("project", row.ProjectID); err != nil {
		return err
	}
	if row.SourceKind != KindCustomAttribute && row.SourceKind != KindSystemAttribute {
		return errors.New("native attribute value has unsupported property kind")
	}
	for name, value := range map[string]string{
		"attribute_key": row.AttributeKey, "attribute_type": row.AttributeType,
	} {
		if err := validateText(name, value, true, MaxPropertyIdentityBytes); err != nil {
			return err
		}
	}
	switch row.AttributeType {
	case "string", "number", "boolean", "array", "map", "json":
	default:
		return errors.New("native attribute value has unsupported attribute type")
	}
	if !isLowerSHA256(row.ValueFingerprint) {
		return errors.New("native attribute value fingerprint is not lowercase SHA-256")
	}
	if err := validateCanonicalJSON("value_json", row.ValueJSON, MaxValueJSONBytes, true); err != nil {
		return err
	}
	decoder := json.NewDecoder(strings.NewReader(row.ValueJSON))
	decoder.UseNumber()
	var decodedValue any
	if err := decoder.Decode(&decodedValue); err != nil {
		return errors.New("native attribute value_json cannot be decoded")
	}
	if err := requireJSONEOF(decoder); err != nil {
		return err
	}
	encodedValue, err := attributecatalog.EncodeScalar(decodedValue)
	if err != nil || encodedValue.ValueJSON != row.ValueJSON ||
		encodedValue.Fingerprint != row.ValueFingerprint {
		return errors.New("native attribute value fingerprint does not match value_json")
	}
	if row.AttributeType == attributecatalog.AttributeTypeMap ||
		row.AttributeType == attributecatalog.AttributeTypeJSON ||
		(row.AttributeType != attributecatalog.AttributeTypeArray && row.AttributeType != encodedValue.Kind) {
		return errors.New("native attribute type does not match scalar value_json kind")
	}
	if err := validateText("value_search_text_folded", row.ValueSearchTextFolded, false, MaxValueSearchTextBytes); err != nil {
		return err
	}
	if row.ValueSearchTextFolded != foldPropertyText(encodedValue.SearchText) {
		return errors.New("native attribute value_search_text_folded does not match value_json")
	}
	if err := validateDateTime64("first_seen", row.FirstSeen); err != nil {
		return err
	}
	if err := validateDateTime64("last_seen", row.LastSeen); err != nil {
		return err
	}
	first, _ := time.Parse(dateTime64Layout, row.FirstSeen)
	last, _ := time.Parse(dateTime64Layout, row.LastSeen)
	if last.Before(first) {
		return errors.New("native attribute last_seen precedes first_seen")
	}
	return nil
}

func validateScope(scope Scope) error {
	if err := validateCanonicalUUID("organization", scope.OrganizationID); err != nil {
		return err
	}
	if err := validateCanonicalUUID("workspace", scope.WorkspaceID); err != nil {
		return err
	}
	if scope.CatalogEpoch == 0 || scope.CatalogRevision == 0 || scope.ProjectionVersion == 0 {
		return errors.New("propertycatalog: epoch, revision, and projection version must be non-zero")
	}
	if err := validateCanonicalUUID("build token", scope.BuildToken); err != nil {
		return err
	}
	if !validSourceAdapter(scope.SourceAdapter) {
		return errors.New("propertycatalog: unsupported source adapter")
	}
	if err := validateCanonicalUUID("producer stream", scope.ProducerStreamID); err != nil {
		return err
	}
	if scope.Sequence == 0 {
		return errors.New("propertycatalog: producer sequence must be non-zero")
	}
	if scope.SourceVersion == 0 || !isLowerSHA256(scope.SourceFingerprint) {
		return errors.New("propertycatalog: source version/fingerprint fence is invalid")
	}
	return nil
}

func validateScopeMatch(
	organizationID, workspaceID string,
	epoch uint16,
	revision uint64,
	buildToken string,
	adapter SourceAdapter,
	streamID string,
	sequence uint64,
	scope Scope,
) error {
	if organizationID != scope.OrganizationID || workspaceID != scope.WorkspaceID {
		return errors.New("row tenant does not match envelope")
	}
	if epoch != scope.CatalogEpoch || revision != scope.CatalogRevision {
		return errors.New("row epoch/revision does not match envelope")
	}
	if buildToken != scope.BuildToken {
		return errors.New("row build token does not match envelope")
	}
	if adapter != scope.SourceAdapter {
		return errors.New("row source adapter does not match envelope")
	}
	if streamID != scope.ProducerStreamID || sequence != scope.Sequence {
		return errors.New("row producer identity does not match envelope")
	}
	return nil
}

func validateVisibility(scope VisibilityScope, id, workspaceID string) error {
	switch scope {
	case VisibilityAlways, VisibilityWorkspace, VisibilityProject, VisibilityAgentDefinition, VisibilityDataset:
	default:
		return errors.New("definition has unsupported visibility scope")
	}
	parsed, err := uuid.Parse(id)
	if err != nil || parsed.String() != id {
		return errors.New("propertycatalog: visibility ID must be a canonical UUID")
	}
	if scope == VisibilityAlways && parsed != uuid.Nil {
		return errors.New("propertycatalog: always visibility must use the zero UUID sentinel")
	}
	if scope == VisibilityWorkspace && id != workspaceID {
		return errors.New("propertycatalog: workspace visibility must match the row workspace")
	}
	if scope != VisibilityAlways && parsed == uuid.Nil {
		return errors.New("propertycatalog: scoped visibility requires a non-zero UUID")
	}
	return nil
}

func validSourceAdapter(adapter SourceAdapter) bool {
	switch adapter {
	case AdapterSystemManifest, AdapterSpanAttribute, AdapterEvalTemplate, AdapterEvalConfig,
		AdapterSimulationEvalConfig, AdapterAnnotationLabel, AdapterDatasetColumn:
		return true
	default:
		return false
	}
}

func validPropertyKind(kind PropertyKind) bool {
	switch kind {
	case KindSystemAttribute, KindCustomAttribute, KindEvalTemplate, KindEvalConfig,
		KindAnnotation, KindDatasetColumn:
		return true
	default:
		return false
	}
}

func validateCanonicalUUID(name, value string) error {
	parsed, err := uuid.Parse(value)
	if err != nil || parsed.String() != value || parsed == uuid.Nil {
		return fmt.Errorf("propertycatalog: %s ID must be a canonical non-nil UUID", name)
	}
	return nil
}

func validateText(name, value string, required bool, maxBytes int) error {
	if (required && value == "") || !utf8.ValidString(value) || len(value) > maxBytes {
		return fmt.Errorf("propertycatalog: %s is empty, invalid UTF-8, or exceeds %d bytes", name, maxBytes)
	}
	for _, r := range value {
		if unicode.IsControl(r) {
			return fmt.Errorf("propertycatalog: %s contains a control character", name)
		}
	}
	return nil
}

func validateDateTime64(name, value string) error {
	parsed, err := time.Parse(dateTime64Layout, value)
	if err != nil || parsed.Format(dateTime64Layout) != value {
		return fmt.Errorf("propertycatalog: %s is not canonical DateTime64(6)", name)
	}
	return nil
}

func validateCanonicalJSON(name, value string, maxBytes int, requireScalar bool) error {
	if value == "" || len(value) > maxBytes || !utf8.ValidString(value) {
		return fmt.Errorf("propertycatalog: %s is empty, invalid UTF-8, or exceeds %d bytes", name, maxBytes)
	}
	decoder := json.NewDecoder(strings.NewReader(value))
	decoder.UseNumber()
	var decoded any
	if err := decoder.Decode(&decoded); err != nil {
		return fmt.Errorf("propertycatalog: decode %s: %w", name, err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return fmt.Errorf("propertycatalog: decode %s: %w", name, err)
	}
	if requireScalar {
		scalar, err := attributecatalog.EncodeScalar(decoded)
		if err != nil {
			return fmt.Errorf("propertycatalog: %s must be a canonical non-null scalar: %w", name, err)
		}
		if scalar.ValueJSON != value {
			return fmt.Errorf("propertycatalog: %s is not deterministic canonical JSON", name)
		}
		return nil
	}
	if _, ok := decoded.(map[string]any); !ok {
		return fmt.Errorf("propertycatalog: %s must be a JSON object", name)
	}
	var canonical bytes.Buffer
	if err := appendCanonicalDefinitionJSON(&canonical, decoded); err != nil {
		return fmt.Errorf("propertycatalog: encode %s: %w", name, err)
	}
	if !bytes.Equal(canonical.Bytes(), []byte(value)) {
		return fmt.Errorf("propertycatalog: %s is not deterministic canonical JSON", name)
	}
	return nil
}

// appendCanonicalDefinitionJSON matches Python json.dumps(sort_keys=True,
// ensure_ascii=False, allow_nan=False, separators=(",", ":")) for the v1
// definition domain. Numbers reuse the span-scalar codec's pinned finite,
// non-exponent, minimal fixed-decimal representation (including -0 -> 0).
func appendCanonicalDefinitionJSON(out *bytes.Buffer, value any) error {
	switch typed := value.(type) {
	case nil:
		out.WriteString("null")
	case bool:
		if typed {
			out.WriteString("true")
		} else {
			out.WriteString("false")
		}
	case string:
		scalar, err := attributecatalog.EncodeScalar(typed)
		if err != nil {
			return err
		}
		out.WriteString(scalar.ValueJSON)
	case json.Number:
		scalar, err := attributecatalog.EncodeScalar(typed)
		if err != nil {
			return err
		}
		out.WriteString(scalar.ValueJSON)
	case []any:
		out.WriteByte('[')
		for index, item := range typed {
			if index != 0 {
				out.WriteByte(',')
			}
			if err := appendCanonicalDefinitionJSON(out, item); err != nil {
				return err
			}
		}
		out.WriteByte(']')
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		out.WriteByte('{')
		for index, key := range keys {
			if index != 0 {
				out.WriteByte(',')
			}
			encodedKey, err := attributecatalog.EncodeScalar(key)
			if err != nil {
				return err
			}
			out.WriteString(encodedKey.ValueJSON)
			out.WriteByte(':')
			if err := appendCanonicalDefinitionJSON(out, typed[key]); err != nil {
				return err
			}
		}
		out.WriteByte('}')
	default:
		return fmt.Errorf("unsupported definition JSON value %T", value)
	}
	return nil
}

func requireJSONEOF(decoder *json.Decoder) error {
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("trailing JSON value")
		}
		return err
	}
	return nil
}

func isLowerSHA256(value string) bool {
	if len(value) != sha256.Size*2 {
		return false
	}
	decoded, err := hex.DecodeString(value)
	return err == nil && hex.EncodeToString(decoded) == value
}
