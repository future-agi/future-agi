package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strings"

	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
)

// The trajectory store's pointer rows (payload_encoding = 'items_v1') keep a
// preview in spans.input/output and replace the attributes_extra copy of each
// extracted side with sentinels. The live collector catalogues such a span
// through its dedup-off attributes; the backfill rebuilds the same view here
// from trajectory_items, so the two catalogs agree.

// trajectoryColumns are the spans columns the backfill reads for pointer
// rows, in SELECT order. The input/output columns are not read: they hold
// previews, and the promoted value is rebuilt from the items.
var trajectoryColumns = []string{
	"input_item_hashes", "output_item_hashes", "tool_def_hashes",
	"item_scope_id", "payload_encoding", "payload_envelope",
}

// sourceSchema is what the source spans table offers the backfill.
type sourceSchema struct {
	// pointers: every 040 pointer column exists, so rows may be items_v1.
	pointers bool
	// jsonExtra: attributes_extra is a ClickHouse JSON column, which returns
	// dotted paths as nested objects.
	jsonExtra bool
}

var itemHashPattern = regexp.MustCompile(`^[0-9a-f]{32}$`)

// itemsPerQuery bounds the (scope, hash) parameters of one items SELECT.
const itemsPerQuery = 256

// schema reads the source spans columns. A source with the pointer columns
// stays that way (040 is additive), so that answer is kept; a source without
// them is asked again on the next page, so rows written after 040 is applied
// mid-scan are still hydrated. Rows that do not describe a column are
// ignored, never an error.
func (s *sourceReader) schema(ctx context.Context) (sourceSchema, error) {
	if s.trajectory.pointers {
		return s.trajectory, nil
	}
	names := []string{"attributes_extra"}
	for _, col := range trajectory.SpansPointerColumns {
		names = append(names, col.Name)
	}
	params := url.Values{}
	placeholders := make([]string, len(names))
	for i, name := range names {
		params.Set(fmt.Sprintf("param_c%d", i), name)
		placeholders[i] = fmt.Sprintf("{c%d:String}", i)
	}
	rows, err := s.selectRows(ctx, `SELECT name, type FROM system.columns WHERE database = currentDatabase() AND table = 'spans' AND name IN (`+strings.Join(placeholders, ", ")+`)`, params)
	if err != nil {
		return sourceSchema{}, err
	}
	types := map[string]string{}
	for _, row := range rows {
		name, okName := row["name"].(string)
		typ, okType := row["type"].(string)
		if okName && okType {
			types[name] = typ
		}
	}
	found := sourceSchema{pointers: true, jsonExtra: strings.HasPrefix(types["attributes_extra"], "JSON")}
	for _, col := range trajectory.SpansPointerColumns {
		if _, ok := types[col.Name]; !ok {
			found.pointers = false
		}
	}
	if found.pointers {
		s.trajectory = found
	}
	return found, nil
}

// promotedSide is one compacted side of a pointer row.
type promotedSide struct {
	key      string
	isJSON   bool
	template *trajectory.SideTemplate
	msgRefs  []string
	toolRefs []string
}

// contentUnavailableMarker tags a logical row whose promoted attribute could
// not be rebuilt. It is not a column: buildPage removes it before extraction
// and records the span as a content gap.
const contentUnavailableMarker = "\x00trajectory_content_unavailable"

// contentUnavailableReason is the receipt reason of a span catalogued
// without its promoted input/output attribute.
const contentUnavailableReason = "trajectory_content_unavailable"

// logicalRows turns the page's latest rows into what dedup off stores for
// catalog purposes: pointer and content columns removed, and on items_v1 rows
// the sentinels replaced by the promoted attribute rebuilt from
// trajectory_items. A side whose items cannot be read gets its sentinels
// removed but its attribute is not catalogued (never the preview); such rows
// are counted in unavailable and tagged with contentUnavailableMarker, so
// the scan records them as a gap. A failed items query is an error, so the
// page is not advanced.
func (s *sourceReader) logicalRows(ctx context.Context, project string, rows []map[string]any, schema sourceSchema) ([]map[string]any, int, error) {
	if !schema.pointers {
		return rows, 0, nil
	}
	type pending struct {
		row   map[string]any
		extra map[string]any
		scope string
		sides []promotedSide
		bad   bool
	}
	var work []*pending
	want := map[[2]string]struct{}{}
	for _, row := range rows {
		encoding, _ := row["payload_encoding"].(string)
		envelope, _ := row["payload_envelope"].(string)
		scope, _ := row["item_scope_id"].(string)
		hashes := map[string][]string{}
		for _, col := range []string{"input_item_hashes", "output_item_hashes", "tool_def_hashes"} {
			hashes[col] = stringList(row[col])
		}
		for _, col := range trajectoryColumns {
			delete(row, col)
		}
		if encoding != trajectory.EncodingItemsV1 {
			continue
		}
		extra, err := decodeExtra(row["attributes_extra"])
		if err != nil {
			return nil, 0, err
		}
		p := &pending{row: row, extra: extra, scope: scope}
		env, envErr := trajectory.ParseEnvelope(envelope)
		for _, side := range []struct {
			keySentinel, jsonSentinel string
			template                  *trajectory.SideTemplate
			msgRefs, toolRefs         []string
		}{
			{trajectory.SentinelPromotedInputKey, trajectory.SentinelPromotedInputJSON, env.Input, hashes["input_item_hashes"], hashes["tool_def_hashes"]},
			{trajectory.SentinelPromotedOutputKey, trajectory.SentinelPromotedOutputJSON, env.Output, hashes["output_item_hashes"], nil},
		} {
			key, ok := extra[side.keySentinel].(string)
			if !ok || key == "" {
				continue
			}
			if envErr != nil || side.template == nil {
				p.bad = true
				continue
			}
			p.sides = append(p.sides, promotedSide{key: key, isJSON: extra[side.jsonSentinel] == true,
				template: side.template, msgRefs: side.msgRefs, toolRefs: side.toolRefs})
			for _, ref := range append(append([]string{}, side.msgRefs...), side.toolRefs...) {
				want[itemRef(scope, ref)] = struct{}{}
			}
			if side.template.SystemHash != "" {
				want[itemRef(scope, side.template.SystemHash)] = struct{}{}
			}
		}
		for _, k := range trajectory.SentinelKeys {
			delete(extra, k)
		}
		row["attributes_extra"] = extra
		work = append(work, p)
	}
	items, err := s.readItems(ctx, project, want)
	if err != nil {
		return nil, 0, err
	}
	byRef := make(map[[2]string]string, len(items))
	for _, it := range items {
		byRef[[2]string{it.ScopeID, it.ItemHash}] = it.Content
	}
	unavailable := 0
	for _, p := range work {
		scope := p.scope
		resolve := func(ref string) (string, bool) {
			content, ok := byRef[itemRef(scope, ref)]
			return content, ok
		}
		missing := p.bad
		for _, side := range p.sides {
			content, err := trajectory.ReconstructSide(side.template, side.msgRefs, side.toolRefs, resolve)
			if err != nil {
				missing = true
				continue
			}
			var value any = content
			if side.isJSON {
				decoder := json.NewDecoder(strings.NewReader(content))
				decoder.UseNumber()
				if err := decoder.Decode(&value); err != nil {
					missing = true
					continue
				}
			}
			setAttribute(p.extra, side.key, value, schema.jsonExtra)
		}
		if missing {
			unavailable++
			p.row[contentUnavailableMarker] = true
		}
	}
	return rows, unavailable, nil
}

// itemRef is the (scope_id, item_hash) a span ref resolves to.
func itemRef(scope, ref string) [2]string {
	if h, ok := strings.CutPrefix(ref, trajectory.ProjectHashPrefix); ok {
		return [2]string{trajectory.ProjectScopeID, h}
	}
	return [2]string{scope, ref}
}

// readItems reads the wanted items of one project, a bounded number of
// (scope, hash) pairs per SELECT. Malformed hashes are not asked for (they
// resolve to nothing).
func (s *sourceReader) readItems(ctx context.Context, project string, want map[[2]string]struct{}) ([]trajectory.Item, error) {
	var refs [][2]string
	for ref := range want {
		if itemHashPattern.MatchString(ref[1]) && ref[0] != "" {
			refs = append(refs, ref)
		}
	}
	var items []trajectory.Item
	for start := 0; start < len(refs); start += itemsPerQuery {
		chunk := refs[start:min(start+itemsPerQuery, len(refs))]
		params := url.Values{"param_project": {project}}
		conditions := make([]string, len(chunk))
		for i, ref := range chunk {
			params.Set(fmt.Sprintf("param_s%d", i), ref[0])
			params.Set(fmt.Sprintf("param_h%d", i), ref[1])
			conditions[i] = fmt.Sprintf("tuple({s%d:String}, {h%d:FixedString(32)})", i, i)
		}
		rows, err := s.selectRows(ctx, `SELECT scope_id, toString(item_hash) AS item_hash, content FROM trajectory_items
 WHERE project_id = {project:UUID}
 AND tuple(scope_id, item_hash) IN (`+strings.Join(conditions, ",")+`)`, params)
		if err != nil {
			return nil, fmt.Errorf("read trajectory items: %w", err)
		}
		for _, row := range rows {
			scope, ok1 := row["scope_id"].(string)
			hash, ok2 := row["item_hash"].(string)
			content, ok3 := row["content"].(string)
			if !ok1 || !ok2 || !ok3 {
				return nil, errors.New("trajectory items SELECT returned an invalid row")
			}
			items = append(items, trajectory.Item{ProjectID: project, ScopeID: scope, ItemHash: hash, Content: content})
		}
	}
	return items, nil
}

// decodeExtra returns attributes_extra as a fresh map, from a JSON column
// (an object) or a String column (JSON text), decoding numbers exactly as
// the catalog extractor does.
func decodeExtra(value any) (map[string]any, error) {
	switch typed := value.(type) {
	case map[string]any:
		out := make(map[string]any, len(typed))
		for k, v := range typed {
			out[k] = v
		}
		return out, nil
	case string:
		decoder := json.NewDecoder(strings.NewReader(typed))
		decoder.UseNumber()
		var out map[string]any
		if err := decoder.Decode(&out); err != nil || out == nil {
			return nil, errors.New("invalid source attributes_extra on a trajectory row")
		}
		return out, nil
	}
	return nil, errors.New("invalid source attributes_extra on a trajectory row")
}

// setAttribute puts key back into attributes_extra. A JSON column returns a
// dotted path as nested objects, so there the key is nested the same way.
func setAttribute(extra map[string]any, key string, value any, nested bool) {
	if !nested || !strings.Contains(key, ".") {
		extra[key] = value
		return
	}
	parts := strings.Split(key, ".")
	node := extra
	for _, part := range parts[:len(parts)-1] {
		next, ok := node[part].(map[string]any)
		if !ok {
			if _, exists := node[part]; exists {
				extra[key] = value // not a path this column could hold
				return
			}
			next = map[string]any{}
			node[part] = next
		}
		node = next
	}
	node[parts[len(parts)-1]] = value
}

// stringList reads a ClickHouse Array(String) value from JSONEachRow.
func stringList(value any) []string {
	list, _ := value.([]any)
	out := make([]string, 0, len(list))
	for _, v := range list {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	return out
}
