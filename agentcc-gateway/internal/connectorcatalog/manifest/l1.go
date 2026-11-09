package manifest

import (
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
)

// ToolListTokenBudget caps the estimated size of a provider's tools/list,
// in tokens, for tools we author (an operations source) and for tier
// optimized. No figure exists in the plans; this one is the lane's choice
// (CN1 NOTES.md). A vendor's own server is measured, not refused.
const ToolListTokenBudget = 20000

// protocolScopes are OpenID Connect scopes a connection asks for that no
// tool needs (refresh tokens and the user's identity).
var protocolScopes = []string{"openid", "offline_access", "profile", "email"}

// draft2020 is the one JSON Schema dialect a tool schema may declare.
const draft2020 = "https://json-schema.org/draft/2020-12/schema"

// legacyKeywords are draft-07 and older keywords that 2020-12 replaced.
var legacyKeywords = map[string]string{
	"definitions":      "JSON Schema 2020-12 replaced with $defs",
	"dependencies":     "JSON Schema 2020-12 split into dependentRequired and dependentSchemas",
	"additionalItems":  "JSON Schema 2020-12 replaced with items",
	"$recursiveRef":    "JSON Schema 2020-12 replaced with $dynamicRef",
	"$recursiveAnchor": "JSON Schema 2020-12 replaced with $dynamicAnchor",
}

// CheckL1 runs L1, the static tool contract, over an L0-clean catalog: the
// four annotations agree, a write tool is never auto and a destructive one
// is denied by default (scaling.md §6), schemas are JSON Schema 2020-12, an
// operations source names each REST call, an oauth2 provider's tool scopes
// together equal the scopes it requests, and our own tools fit the
// tools/list token budget. The semver diff is SemverDiff, run against the
// last published version.
func CheckL1(c *Catalog) error {
	var errs []error
	for _, a := range c.Apps {
		for _, p := range a.Providers {
			errs = append(errs, checkProviderL1(p)...)
		}
	}
	return errors.Join(errs...)
}

func checkProviderL1(p *Provider) []error {
	var errs []error
	tools := p.AllTools()
	for i, t := range tools {
		path := p.toolFile(i)
		fail := func(format string, args ...any) {
			errs = append(errs, fmt.Errorf("%s: %s", path, fmt.Sprintf(format, args...)))
		}
		ann := t.Annotations
		if ann.ReadOnlyHint && ann.DestructiveHint {
			fail("tool %s is readOnlyHint and destructiveHint at once", t.Name)
		}
		posture := t.EffectivePosture(p.Tools.DefaultPosture)
		switch {
		case ann.DestructiveHint && posture != PostureDeny:
			fail("tool %s is destructive, so its posture is deny", t.Name)
		case !ann.ReadOnlyHint && posture == PostureAuto:
			fail("tool %s writes, so its posture is ask or deny, not auto", t.Name)
		}
		for _, s := range []struct {
			name string
			doc  json.RawMessage
		}{{"inputSchema", t.InputSchema}, {"outputSchema", t.OutputSchema}} {
			if len(s.doc) > 0 {
				for _, msg := range schemaDialect(s.name, s.doc) {
					fail("tool %s: %s", t.Name, msg)
				}
			}
		}
		if p.Tools.Source == ToolsOperations {
			if t.Operation == nil {
				fail("tool %s has no operation; every tool of an operations source names its REST call", t.Name)
			}
			if t.Description == "" {
				fail("tool %s has no description", t.Name)
			}
			if len(t.InputSchema) == 0 {
				fail("tool %s has no inputSchema", t.Name)
			}
		} else if t.Operation != nil || t.Response != nil {
			fail("tool %s has an operation, but only an operations source makes REST calls", t.Name)
		}
	}
	errs = append(errs, checkScopeUnion(p, tools)...)
	if p.Tools.Source == ToolsOperations || p.Quality.Tier == "optimized" {
		if n := ToolListTokens(tools); n > ToolListTokenBudget {
			errs = append(errs, fmt.Errorf("%s: tools/list is about %d tokens; the budget is %d", p.path, n, ToolListTokenBudget))
		}
	}
	return errs
}

// checkScopeUnion requires the scopes an oauth2 provider's tools need,
// together, to equal the scopes it requests, apart from protocol scopes.
func checkScopeUnion(p *Provider, tools []Tool) []error {
	var errs []error
	o := p.Auth.OAuth2
	if o == nil {
		for i, t := range tools {
			if t.Scopes != nil {
				errs = append(errs, fmt.Errorf("%s: tool %s lists scopes, but the provider does not use oauth2", p.toolFile(i), t.Name))
			}
		}
		return errs
	}
	if len(tools) == 0 {
		return nil // tools are discovered live; nothing to compare
	}
	needed := map[string]bool{}
	for i, t := range tools {
		if t.Scopes == nil {
			errs = append(errs, fmt.Errorf("%s: tool %s declares no scopes; an oauth2 provider's tools each list the scopes they need", p.toolFile(i), t.Name))
		}
		for _, s := range t.Scopes {
			needed[s] = true
			if !slices.Contains(o.Scopes, s) {
				errs = append(errs, fmt.Errorf("%s: tool %s needs %s, which auth.oauth2.scopes does not request", p.toolFile(i), t.Name, s))
			}
		}
	}
	for _, s := range o.Scopes {
		if !needed[s] && !slices.Contains(protocolScopes, s) {
			errs = append(errs, fmt.Errorf("%s: auth.oauth2.scopes requests %s, which no tool needs", p.path, s))
		}
	}
	return errs
}

// schemaDialect lists why a tool schema is not plain JSON Schema 2020-12:
// another declared dialect, a draft-07 keyword, items as an array, a remote
// reference, or an inputSchema whose root is not an object.
func schemaDialect(name string, doc json.RawMessage) []string {
	var root map[string]any
	if err := json.Unmarshal(doc, &root); err != nil {
		return []string{fmt.Sprintf("%s is not a JSON object", name)}
	}
	var out []string
	if d, ok := root["$schema"]; ok && d != draft2020 {
		out = append(out, fmt.Sprintf("%s declares %v; only %s is accepted", name, d, draft2020))
	}
	if name == "inputSchema" && root["type"] != "object" {
		out = append(out, "inputSchema's root must have type object")
	}
	walkSchema(root, func(keyword string, value any) {
		if why, legacy := legacyKeywords[keyword]; legacy {
			out = append(out, fmt.Sprintf("%s uses %s, which %s", name, keyword, why))
		}
		if _, isArray := value.([]any); isArray && keyword == "items" {
			out = append(out, fmt.Sprintf("%s uses items as an array, which JSON Schema 2020-12 replaced with prefixItems", name))
		}
		if ref, ok := value.(string); ok && keyword == "$ref" && !strings.HasPrefix(ref, "#") {
			out = append(out, fmt.Sprintf("%s references %s; only local references are allowed", name, ref))
		}
	})
	return out
}

// Keywords whose value is a subschema, a map of them, or a list of them.
var (
	subschemaKeywords = []string{"items", "additionalItems", "additionalProperties", "not", "if", "then", "else",
		"contains", "propertyNames", "unevaluatedItems", "unevaluatedProperties"}
	schemaMapKeywords  = []string{"properties", "patternProperties", "$defs", "definitions", "dependentSchemas", "dependencies"}
	schemaListKeywords = []string{"allOf", "anyOf", "oneOf", "prefixItems", "items"}
)

// walkSchema calls visit with each keyword of a schema object and of every
// subschema in it. Property names, enum values and defaults are data, not
// keywords, so they are never visited as keywords.
func walkSchema(schema map[string]any, visit func(keyword string, value any)) {
	for _, k := range sortedNames(schema) {
		v := schema[k]
		visit(k, v)
		switch {
		case slices.Contains(schemaMapKeywords, k):
			if m, ok := v.(map[string]any); ok {
				for _, name := range sortedNames(m) {
					if sub, ok := m[name].(map[string]any); ok {
						walkSchema(sub, visit)
					}
				}
			}
		case slices.Contains(subschemaKeywords, k) || slices.Contains(schemaListKeywords, k):
			switch x := v.(type) {
			case map[string]any:
				walkSchema(x, visit)
			case []any:
				for _, e := range x {
					if sub, ok := e.(map[string]any); ok {
						walkSchema(sub, visit)
					}
				}
			}
		}
	}
}

// mcpTool is a tool as tools/list sends it.
type mcpTool struct {
	Name         string          `json:"name"`
	Title        string          `json:"title,omitempty"`
	Description  string          `json:"description,omitempty"`
	InputSchema  json.RawMessage `json:"inputSchema"`
	OutputSchema json.RawMessage `json:"outputSchema,omitempty"`
	Annotations  Annotations     `json:"annotations"`
}

// ToolListTokens estimates the tokens of a tools/list carrying tools, as M30
// does: characters / 4 (MCP-PLAN.md:773), rounded up.
func ToolListTokens(tools []Tool) int {
	chars := 0
	for _, t := range tools {
		in := t.InputSchema
		if len(in) == 0 {
			in = json.RawMessage(`{"type":"object"}`)
		}
		data, _ := json.Marshal(mcpTool{t.Name, t.Title, t.Description, in, t.OutputSchema, t.Annotations})
		chars += len([]rune(string(data)))
	}
	return (chars + 3) / 4
}
