package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/google/uuid"
	"golang.org/x/text/cases"
)

type legacyRowsReader interface {
	selectRows(context.Context, string, url.Values) ([]map[string]any, error)
}

// These are historical source selectors, not versions of the new catalog.
// Main authenticates Scope through PG and gates publication on --verified-legacy.
type legacySelection struct {
	Scope      observedcatalog.Scope
	Epoch      uint16
	Revision   uint64
	BuildToken string
}

// Main must bind a persisted cursor to the complete selection and source DB.
// The zero cursor starts with definitions, including keys without values.
type legacyCursor struct {
	Values                                                               bool
	DefinitionBinding                                                    string
	SourceKind, AttributeKey, AttributeType, ValueFingerprint, ValueJSON string
	Done                                                                 bool
}

const legacyWhere = ` organization_id = {org:UUID} AND workspace_id = {workspace:UUID}
 AND catalog_epoch = {epoch:UInt16} AND catalog_revision = {revision:UInt64}
 AND build_token = {build:UUID}`

const legacyDefinitionWhere = legacyWhere + ` AND source_adapter = 'span_attribute'
 AND visibility_scope = 'project' AND visibility_id = {project:UUID}`

// JSONEachRow encodes UUIDs as strings. Same-name toString aliases would rebind
// the UUID predicates in ClickHouse and make their comparisons ill-typed.
const legacyScopeColumns = `organization_id, workspace_id, catalog_epoch, catalog_revision, build_token, `

// A page contains at most 1000 identities. SELECT resource limits are enforced
// by sourceReader; aggregation keeps duplicate physical rows bounded in memory.
// Errors return no rows and the original cursor. Earlier pages may already have
// been published; an error never certifies a complete historical import.
func importLegacyPage(ctx context.Context, reader legacyRowsReader, selection legacySelection, after legacyCursor, pageSize int) (observedcatalog.Batch, legacyCursor, error) {
	if err := selection.validate(); err != nil {
		return observedcatalog.Batch{}, after, err
	}
	if pageSize < 1 || pageSize > 1000 || reader == nil {
		return observedcatalog.Batch{}, after, errors.New("legacy: reader and page size in [1,1000] required")
	}
	if err := ctx.Err(); err != nil {
		return observedcatalog.Batch{}, after, err
	}
	if after.Done {
		return observedcatalog.Batch{}, after, nil
	}
	params := selection.params()
	params.Set("param_limit", strconv.Itoa(pageSize))
	var batch observedcatalog.Batch
	next := after
	var err error
	if after.Values {
		batch, next, err = legacyValuesPage(ctx, reader, selection, after, pageSize, params)
	} else {
		params.Set("param_binding", after.DefinitionBinding)
		var rows []map[string]any
		rows, err = reader.selectRows(ctx, legacyDefinitionsSQL("binding_id > {binding:String}"), params)
		if err == nil && len(rows) > pageSize {
			err = errors.New("legacy: definition page exceeded requested size")
		}
		for _, row := range rows {
			if err != nil {
				break
			}
			var keys []observedcatalog.KeyRow
			var binding string
			keys, binding, err = legacyDefinition(row, selection)
			if err == nil && binding <= next.DefinitionBinding {
				err = errors.New("legacy: definition page is not strictly ordered")
			}
			batch.Keys = append(batch.Keys, keys...)
			next.DefinitionBinding = binding
		}
		if len(rows) < pageSize {
			next.Values = true
		}
	}
	if err == nil {
		batch = observedcatalog.Merge(batch)
		err = batch.Validate()
	}
	if err != nil {
		return observedcatalog.Batch{}, after, err
	}
	return batch, next, nil
}

func (s legacySelection) validate() error {
	for _, value := range []string{s.Scope.OrganizationID, s.Scope.WorkspaceID, s.Scope.ProjectID, s.BuildToken} {
		id, err := uuid.Parse(value)
		if err != nil || id == uuid.Nil || id.String() != value {
			return errors.New("legacy: canonical nonzero scope and build UUIDs required")
		}
	}
	if s.Epoch == 0 || s.Revision == 0 {
		return errors.New("legacy: explicit nonzero epoch/revision/build selection required")
	}
	return nil
}

func (s legacySelection) params() url.Values {
	return url.Values{
		"param_org": {s.Scope.OrganizationID}, "param_workspace": {s.Scope.WorkspaceID},
		"param_project": {s.Scope.ProjectID}, "param_epoch": {fmt.Sprint(s.Epoch)},
		"param_revision": {fmt.Sprint(s.Revision)}, "param_build": {s.BuildToken},
	}
}

// Select all distinct latest semantic states before checking tombstones. The
// two-state cap proves a conflict without collecting unbounded delivery copies.
// Neither FINAL nor an arbitrary argMax winner is safe on this plain MergeTree.
func legacyDefinitionsSQL(predicate string) string {
	return `SELECT ` + legacyScopeColumns + `toString(visibility_id) AS project_id, binding_id,
 groupUniqArray(2)(tuple(source_entity_id, property_kind, definition_json, definition_sha256,
 toString(first_seen), toString(last_seen), is_deleted, toString(deleted_at),
 state_sha256, source_fingerprint, projection_version, name, property_id)) AS states
 FROM property_definition_catalog PREWHERE` + legacyDefinitionWhere + `
 WHERE tuple(binding_id, source_version) IN (
 SELECT binding_id, max(source_version) FROM property_definition_catalog PREWHERE` + legacyDefinitionWhere + `
 WHERE ` + predicate + ` GROUP BY binding_id ORDER BY binding_id LIMIT {limit:UInt32})
 GROUP BY organization_id, workspace_id, catalog_epoch, catalog_revision, build_token, visibility_id, binding_id
 ORDER BY binding_id LIMIT {limit:UInt32}`
}

func legacyDefinition(row map[string]any, selection legacySelection) ([]observedcatalog.KeyRow, string, error) {
	if err := legacyCheckSelection(row, selection); err != nil {
		return nil, "", err
	}
	binding, ok := row["binding_id"].(string)
	if !ok || !legacyDigest(binding) {
		return nil, "", errors.New("legacy: invalid definition binding")
	}
	states, ok := row["states"].([]any)
	if !ok || len(states) != 1 {
		return nil, "", errors.New("legacy: conflicting or missing latest definition states")
	}
	state, ok := states[0].([]any)
	if !ok || len(state) != 13 {
		return nil, "", errors.New("legacy: unsupported definition state format")
	}
	deleted, err := legacyUint(state[6])
	if err != nil || deleted != 0 || state[7] != nil {
		return nil, "", errors.New("legacy: deleted or tombstoned definition cannot be imported")
	}
	key, keyOK := state[0].(string)
	raw, rawOK := state[2].(string)
	if !keyOK || !rawOK || len(raw) > 32768 || state[1] != "custom_attribute" || state[11] != key {
		return nil, "", errors.New("legacy: unsupported span_attribute definition")
	}
	if fmt.Sprintf("%x", sha256.Sum256([]byte(raw))) != state[3] {
		return nil, "", errors.New("legacy: definition JSON digest mismatch")
	}
	var definition struct {
		Name, PropertyKind, DefinitionSource, ValueAdapter, PropertyID, ValueType, OutputType string
		Details                                                                               struct {
			AttributeTypes      []string `json:"attribute_types"`
			AttributeTypesExact bool     `json:"attribute_types_exact"`
			DataType            string   `json:"data_type"`
		} `json:"details"`
	}
	// Use the actual source_adapters definition_json field names, not a guessed
	// top-level attribute_type. Unknown metadata is irrelevant to observations.
	var fields map[string]json.RawMessage
	if json.Unmarshal([]byte(raw), &fields) != nil || fields == nil {
		return nil, "", errors.New("legacy: invalid definition JSON")
	}
	for name, dest := range map[string]any{
		"name": &definition.Name, "property_kind": &definition.PropertyKind,
		"definition_source": &definition.DefinitionSource, "value_adapter": &definition.ValueAdapter,
		"property_id": &definition.PropertyID, "value_type": &definition.ValueType,
		"output_type": &definition.OutputType, "details": &definition.Details,
	} {
		if json.Unmarshal(fields[name], dest) != nil {
			return nil, "", errors.New("legacy: unsupported definition JSON fields")
		}
	}
	types := definition.Details.AttributeTypes
	if definition.Name != key || definition.PropertyKind != "custom_attribute" ||
		definition.DefinitionSource != "span_attribute_value_catalog" || definition.ValueAdapter != "span_attribute_value" ||
		definition.PropertyID != state[12] || !definition.Details.AttributeTypesExact || len(types) == 0 || len(types) > 6 {
		return nil, "", errors.New("legacy: unsupported or inexact span_attribute definition")
	}
	resolved := "json"
	if len(types) == 1 {
		resolved = types[0]
	}
	if definition.ValueType != resolved || definition.OutputType != resolved || definition.Details.DataType != resolved {
		return nil, "", errors.New("legacy: conflicting definition types")
	}
	first, err1 := legacyTime(state[4])
	last, err2 := legacyTime(state[5])
	if err1 != nil || err2 != nil {
		return nil, "", errors.New("legacy: missing or invalid definition observation interval")
	}
	keys := make([]observedcatalog.KeyRow, 0, len(types))
	for i, typ := range types {
		if slices.Contains(types[:i], typ) {
			return nil, "", errors.New("legacy: duplicate definition attribute types")
		}
		keys = append(keys, observedcatalog.KeyRow{Scope: selection.Scope, SourceKind: "custom_attribute",
			AttributeKey: key, AttributeType: typ, KeyFolded: cases.Fold().String(key), FirstSeen: first, LastSeen: last})
	}
	return keys, binding, (observedcatalog.Batch{Keys: keys}).Validate()
}

func legacyValuesPage(ctx context.Context, reader legacyRowsReader, selection legacySelection, after legacyCursor, pageSize int, params url.Values) (observedcatalog.Batch, legacyCursor, error) {
	for i, field := range []string{"kind", "key", "type", "fingerprint", "json"} {
		params.Set("param_"+field, after.valueKey()[i])
	}
	rows, err := reader.selectRows(ctx, `SELECT `+legacyScopeColumns+`project_id,
 source_kind, attribute_key, attribute_type,
 value_fingerprint, value_json, groupUniqArray(2)(value_search_text_folded) AS searches,
 toString(min(first_seen)) AS first_seen, toString(max(last_seen)) AS last_seen
 FROM span_attribute_value_catalog PREWHERE`+legacyWhere+` AND project_id = {project:UUID}
 WHERE tuple(toString(source_kind), attribute_key, toString(attribute_type), value_fingerprint, value_json)
 > tuple({kind:String}, {key:String}, {type:String}, {fingerprint:String}, {json:String})
 GROUP BY organization_id, workspace_id, project_id, catalog_epoch, catalog_revision, build_token,
 source_kind, attribute_key, attribute_type, value_fingerprint, value_json
 ORDER BY toString(source_kind), attribute_key, toString(attribute_type), value_fingerprint, value_json
 LIMIT {limit:UInt32}`, params)
	if err != nil {
		return observedcatalog.Batch{}, after, err
	}
	if len(rows) > pageSize {
		return observedcatalog.Batch{}, after, errors.New("legacy: value page exceeded requested size")
	}
	batch := observedcatalog.Batch{}
	next := after
	custom := map[string]bool{}
	for _, row := range rows {
		if err := legacyCheckSelection(row, selection); err != nil {
			return observedcatalog.Batch{}, after, err
		}
		value := observedcatalog.ValueRow{Scope: selection.Scope}
		for name, dest := range map[string]*string{
			"source_kind": &value.SourceKind, "attribute_key": &value.AttributeKey, "attribute_type": &value.AttributeType,
			"value_fingerprint": &value.ValueFingerprint, "value_json": &value.ValueJSON,
		} {
			text, ok := row[name].(string)
			if !ok {
				return observedcatalog.Batch{}, after, errors.New("legacy: invalid value identity")
			}
			*dest = text
		}
		searches, ok := row["searches"].([]any)
		if !ok || len(searches) != 1 {
			return observedcatalog.Batch{}, after, errors.New("legacy: conflicting value search payloads")
		}
		value.ValueSearchTextFolded, ok = searches[0].(string)
		first, err1 := legacyTime(row["first_seen"])
		last, err2 := legacyTime(row["last_seen"])
		if !ok || err1 != nil || err2 != nil {
			return observedcatalog.Batch{}, after, errors.New("legacy: invalid value payload")
		}
		value.FirstSeen, value.LastSeen = first, last
		if value.SourceKind == attributecatalog.SourceKindCustomAttribute {
			custom[value.AttributeKey] = true
		} else if value.SourceKind != attributecatalog.SourceKindSystemAttribute || value.AttributeKey != "model" || value.AttributeType != "string" {
			return observedcatalog.Batch{}, after, errors.New("legacy: unsupported system value (only model/string is supported)")
		}
		candidate := legacyCursor{Values: true, SourceKind: value.SourceKind, AttributeKey: value.AttributeKey,
			AttributeType: value.AttributeType, ValueFingerprint: value.ValueFingerprint, ValueJSON: value.ValueJSON}
		if slices.Compare(candidate.valueKey(), next.valueKey()) <= 0 {
			return observedcatalog.Batch{}, after, errors.New("legacy: value page is not strictly ordered")
		}
		next = candidate
		batch.Values = append(batch.Values, value)
		batch.Keys = append(batch.Keys, observedcatalog.KeyRow{Scope: selection.Scope, SourceKind: value.SourceKind,
			AttributeKey: value.AttributeKey, AttributeType: value.AttributeType, KeyFolded: cases.Fold().String(value.AttributeKey),
			FirstSeen: first, LastSeen: last})
	}
	// Validate canonical JSON, scalar type, exact fingerprint and folded search
	// with observedcatalog's attributecatalog codec before another source read.
	if err := batch.Validate(); err != nil {
		return observedcatalog.Batch{}, after, err
	}
	if len(custom) > 0 {
		lookup := selection.params()
		keys := make([]string, 0, len(custom))
		for key := range custom {
			keys = append(keys, key)
		}
		slices.Sort(keys)
		placeholders := make([]string, len(keys))
		for i, key := range keys {
			name := fmt.Sprintf("key%d", i)
			lookup.Set("param_"+name, key)
			placeholders[i] = "{" + name + ":String}"
		}
		lookup.Set("param_limit", strconv.Itoa(len(keys)+1))
		// Locate bindings by historical entity, then resolve their latest version
		// across the selected tuple. Filtering entity before max could resurrect
		// an old live payload when the latest state erased its entity metadata.
		predicate := `binding_id IN (SELECT binding_id FROM property_definition_catalog PREWHERE` + legacyDefinitionWhere +
			` WHERE source_entity_id IN (` + strings.Join(placeholders, ",") + `))`
		definitions, err := reader.selectRows(ctx, legacyDefinitionsSQL(predicate), lookup)
		if err != nil {
			return observedcatalog.Batch{}, after, err
		}
		if len(definitions) != len(keys) {
			return observedcatalog.Batch{}, after, errors.New("legacy: missing, erased or ambiguous definitions for custom values")
		}
		allowed := map[string][]string{}
		for _, row := range definitions {
			defKeys, _, err := legacyDefinition(row, selection)
			if err != nil {
				return observedcatalog.Batch{}, after, err
			}
			key := defKeys[0].AttributeKey
			if !custom[key] || allowed[key] != nil {
				return observedcatalog.Batch{}, after, errors.New("legacy: unrequested or ambiguous custom definition")
			}
			for _, k := range defKeys {
				allowed[key] = append(allowed[key], k.AttributeType)
			}
		}
		for _, value := range batch.Values {
			if value.SourceKind == "custom_attribute" && !slices.Contains(allowed[value.AttributeKey], value.AttributeType) {
				return observedcatalog.Batch{}, after, errors.New("legacy: value type is absent from latest definition")
			}
		}
	}
	next.Done = len(rows) < pageSize
	return batch, next, nil
}

func (c legacyCursor) valueKey() []string {
	return []string{c.SourceKind, c.AttributeKey, c.AttributeType, c.ValueFingerprint, c.ValueJSON}
}

func legacyCheckSelection(row map[string]any, selection legacySelection) error {
	epoch, err1 := legacyUint(row["catalog_epoch"])
	revision, err2 := legacyUint(row["catalog_revision"])
	if err1 != nil || err2 != nil || epoch != uint64(selection.Epoch) || revision != selection.Revision ||
		row["organization_id"] != selection.Scope.OrganizationID || row["workspace_id"] != selection.Scope.WorkspaceID ||
		row["project_id"] != selection.Scope.ProjectID || row["build_token"] != selection.BuildToken {
		return errors.New("legacy: source row is outside the explicitly selected scope/epoch/revision/build")
	}
	return nil
}

func legacyUint(value any) (uint64, error) {
	switch v := value.(type) {
	case string:
		return strconv.ParseUint(v, 10, 64)
	case json.Number:
		return strconv.ParseUint(string(v), 10, 64)
	default:
		return 0, errors.New("legacy: invalid unsigned integer")
	}
}

func legacyTime(value any) (string, error) {
	text, ok := value.(string)
	if ok {
		// ClickHouse toString(DateTime64(6)) may omit trailing fractional zeros.
		if parsed, err := time.Parse("2006-01-02 15:04:05.999999", text); err == nil {
			return parsed.Format(observedcatalog.TimeLayout), nil
		}
	}
	return "", errors.New("legacy: invalid observation timestamp")
}

func legacyDigest(value string) bool {
	return len(value) == 64 && strings.Trim(value, "0123456789abcdef") == ""
}
