package manifest

import (
	"encoding/json"
	"fmt"
	"slices"
	"strings"
)

// prefixed returns errs with path in front of each message.
func prefixed(path string, errs []error) []error {
	out := make([]error, len(errs))
	for i, err := range errs {
		out[i] = fmt.Errorf("%s: %w", path, err)
	}
	return out
}

// decodeRecord reads one record file into T: strict YAML (or JSON for
// .json files), its shape, then the source rules against the raw document.
func decodeRecord[T any](path string, data []byte) (*T, map[string]any, []error) {
	var raw any
	var err error
	if strings.HasSuffix(path, ".json") {
		raw, err = decodeJSON(data)
	} else {
		raw, err = decodeYAML(data)
	}
	if err != nil {
		return nil, nil, []error{fmt.Errorf("%s: %w", path, err)}
	}
	obj, ok := raw.(map[string]any)
	if !ok {
		return nil, nil, []error{fmt.Errorf("%s: must be an object", path)}
	}
	rec, errs := decodeShaped[T](obj)
	if len(errs) > 0 {
		return nil, nil, prefixed(path, errs)
	}
	return rec, obj, nil
}

func parseApp(path string, data []byte) (*App, []error) {
	a, raw, errs := decodeRecord[App](path, data)
	if a == nil {
		return nil, errs
	}
	a.path = path
	errs = append(errs, checkLifecycle(a.Lifecycle.Status, a.Lifecycle.SunsetAt)...)
	if a.ID == "platform" {
		errs = append(errs, fmt.Errorf("app id platform is reserved: price-overlay keys use platform/<platform-id>"))
	}
	if a.Lifecycle.ReplacedBy == a.ID {
		errs = append(errs, fmt.Errorf("lifecycle.replacedBy names the app itself"))
	}
	errs = append(errs, checkSources(raw, a.Sources, appFacts(a))...)
	return a, prefixed(path, errs)
}

func parsePlatform(path string, data []byte) (*Platform, []error) {
	p, raw, errs := decodeRecord[Platform](path, data)
	if p == nil {
		return nil, errs
	}
	p.path = path
	errs = append(errs, checkSources(raw, p.Sources, []string{"connect.urlPattern"})...)
	return p, prefixed(path, errs)
}

func parseProvider(path string, data []byte) (*Provider, []error) {
	p, raw, errs := decodeRecord[Provider](path, data)
	if p == nil {
		return nil, errs
	}
	p.path = path
	errs = append(errs, checkProvider(p)...)
	errs = append(errs, checkSources(raw, p.Sources, providerFacts(p))...)
	for i := range p.Tools.Items {
		errs = append(errs, checkTool(fmt.Sprintf("/tools/items/%d", i), &p.Tools.Items[i])...)
	}
	return p, prefixed(path, errs)
}

func parseGenerated(path string, data []byte) (*ToolsGenerated, []error) {
	g, _, errs := decodeRecord[ToolsGenerated](path, data)
	if g == nil {
		return nil, errs
	}
	for i := range g.Tools {
		errs = append(errs, checkTool(fmt.Sprintf("/tools/%d", i), &g.Tools[i])...)
	}
	return g, prefixed(path, errs)
}

func checkLifecycle(status, sunsetAt string) []error {
	if status == "sunset" && sunsetAt == "" {
		return []error{fmt.Errorf("/lifecycle/sunsetAt: is required when status is sunset")}
	}
	return nil
}

// checkTool holds the per-tool rules that need no other record.
func checkTool(path string, t *Tool) []error {
	var errs []error
	if len(t.GenericArguments) > 0 && !t.Generic {
		errs = append(errs, fmt.Errorf("%s/genericArguments: needs generic: true", path))
	}
	if len(t.GenericArguments) > 0 && len(t.InputSchema) > 0 {
		props := inputProperties(t.InputSchema)
		for _, arg := range t.GenericArguments {
			if !slices.Contains(props, arg) {
				errs = append(errs, fmt.Errorf("%s/genericArguments: genericArguments names %s, which is not an inputSchema property", path, arg))
			}
		}
	}
	return errs
}

// inputProperties returns the top-level property names of a JSON Schema.
func inputProperties(schema json.RawMessage) []string {
	var doc struct {
		Properties map[string]json.RawMessage `json:"properties"`
	}
	if json.Unmarshal(schema, &doc) != nil {
		return nil
	}
	names := make([]string, 0, len(doc.Properties))
	for name := range doc.Properties {
		names = append(names, name)
	}
	slices.Sort(names)
	return names
}
