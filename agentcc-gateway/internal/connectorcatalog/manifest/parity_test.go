package manifest

import (
	"encoding/json"
	"reflect"
	"regexp"
	"slices"
	"testing"

	"github.com/futureagi/agentcc-gateway/catalog/connectors/schema"
)

// defNames maps each record type to its $defs entry in the manifest schema.
var defNames = map[reflect.Type]string{
	reflect.TypeFor[App](): "app", reflect.TypeFor[Vendor](): "vendor", reflect.TypeFor[Icon](): "icon",
	reflect.TypeFor[Lifecycle](): "lifecycle", reflect.TypeFor[Source](): "source", reflect.TypeFor[AppXOmega](): "appXOmega",
	reflect.TypeFor[AppXGateway](): "appXGateway", reflect.TypeFor[Platform](): "platform", reflect.TypeFor[PlatformConnect](): "platformConnect",
	reflect.TypeFor[Provider](): "provider", reflect.TypeFor[VendorStatus](): "vendorStatus", reflect.TypeFor[Transport](): "transport",
	reflect.TypeFor[URLVariable](): "urlVariable", reflect.TypeFor[Package](): "package", reflect.TypeFor[Auth](): "auth",
	reflect.TypeFor[OAuth2](): "oauth2", reflect.TypeFor[Refresh](): "refresh", reflect.TypeFor[Revocation](): "revocation",
	reflect.TypeFor[OAuthReview](): "oauthReview", reflect.TypeFor[TokenResponse](): "tokenResponse",
	reflect.TypeFor[CredentialPart](): "credentialPart", reflect.TypeFor[ClientApproval](): "clientApproval",
	reflect.TypeFor[PartnerAuth](): "partnerAuth", reflect.TypeFor[Verify](): "verify", reflect.TypeFor[VerifyHTTP](): "verifyHttp",
	reflect.TypeFor[RateLimit](): "rateLimit", reflect.TypeFor[Retry](): "retry", reflect.TypeFor[Pricing](): "pricing",
	reflect.TypeFor[Upstream](): "upstream", reflect.TypeFor[RegistryRef](): "registryRef", reflect.TypeFor[Spec](): "spec",
	reflect.TypeFor[Quality](): "quality", reflect.TypeFor[Conformance](): "conformance", reflect.TypeFor[LiveSmoke](): "liveSmoke",
	reflect.TypeFor[Evals](): "evals", reflect.TypeFor[QualityReview](): "qualityReview",
	reflect.TypeFor[ProviderLifecycle](): "providerLifecycle", reflect.TypeFor[ProviderXOmega](): "providerXOmega",
	reflect.TypeFor[PublicClient](): "publicClient", reflect.TypeFor[PublicClientEvidence](): "publicClientEvidence", reflect.TypeFor[ConnectionEnablementDeclaration](): "connectionEnablement",
	reflect.TypeFor[ProviderXGateway](): "providerXGateway", reflect.TypeFor[Tools](): "tools", reflect.TypeFor[Snapshot](): "snapshot",
	reflect.TypeFor[Tool](): "tool", reflect.TypeFor[Annotations](): "annotations", reflect.TypeFor[Operation](): "operation",
	reflect.TypeFor[Response](): "response", reflect.TypeFor[ToolsGenerated](): "toolsGenerated", reflect.TypeFor[GeneratedSpec](): "generatedSpec",
}

func loadSchemaDoc(t *testing.T, name string) map[string]any {
	t.Helper()
	data, err := schema.Files.ReadFile(name)
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatalf("%s: %v", name, err)
	}
	return doc
}

func ref(def string) map[string]any { return map[string]any{"$ref": "#/$defs/" + def} }

// expectedProperty is the JSON Schema a field's type and tag stand for.
func expectedProperty(t *testing.T, typ reflect.Type, c constraint) map[string]any {
	t.Helper()
	if typ.Kind() == reflect.Pointer {
		typ = typ.Elem()
	}
	bounds := func(m map[string]any, minKey, maxKey string, lo, hi int) map[string]any {
		if lo > 0 {
			m[minKey] = float64(lo)
		}
		if hi >= 0 {
			m[maxKey] = float64(hi)
		}
		return m
	}
	scalar := func(typ reflect.Type, c constraint) map[string]any {
		switch typ.Kind() {
		case reflect.String:
			switch {
			case c.hasConst:
				return map[string]any{"const": c.constant}
			case c.ref != "":
				return ref(c.ref)
			}
			return bounds(map[string]any{"type": "string"}, "minLength", "maxLength", c.minLen, c.maxLen)
		case reflect.Bool:
			return map[string]any{"type": "boolean"}
		case reflect.Int, reflect.Float64:
			m := map[string]any{"type": map[reflect.Kind]string{reflect.Int: "integer", reflect.Float64: "number"}[typ.Kind()]}
			if c.hasRange {
				m["minimum"], m["maximum"] = c.min, c.max
			}
			return m
		case reflect.Struct:
			return ref(defNames[typ])
		}
		t.Fatalf("no expectation for %s", typ)
		return nil
	}
	switch {
	case typ == rawMessageType:
		return map[string]any{"type": "object"}
	case typ.Kind() == reflect.Slice:
		elem := constraint{ref: c.ref, minLen: c.minElem, maxLen: c.maxElem, min: c.min, max: c.max, hasRange: c.hasRange}
		m := bounds(map[string]any{"type": "array", "items": scalar(typ.Elem(), elem)}, "minItems", "maxItems", c.minLen, c.maxLen)
		if c.unique {
			m["uniqueItems"] = true
		}
		return m
	case typ.Kind() == reflect.Map:
		value := scalar(typ.Elem(), constraint{minLen: c.minElem, maxLen: c.maxElem})
		m := bounds(map[string]any{"type": "object", "additionalProperties": value}, "minProperties", "maxProperties", c.minLen, c.maxLen)
		if c.keys != "" {
			m["propertyNames"] = ref(c.keys)
		}
		return m
	}
	return scalar(typ, c)
}

// withoutAnnotations drops keys that document a schema without constraining it.
func withoutAnnotations(m map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range m {
		if k != "description" && k != "title" {
			out[k] = v
		}
	}
	return out
}

// TestSchemaParity holds the strict loader equal to the published schema:
// each record type's keys, required keys and per-key constraints, and each
// string definition's pattern, enum, refused values and length cap.
func TestSchemaParity(t *testing.T) {
	doc := loadSchemaDoc(t, schema.Manifest)
	defs := doc["$defs"].(map[string]any)
	covered := map[string]bool{}

	for name, d := range stringDefs {
		covered[name] = true
		def, ok := defs[name].(map[string]any)
		if !ok {
			t.Errorf("stringDefs[%s] has no $def", name)
			continue
		}
		want := map[string]any{}
		if d.pattern != nil {
			want["type"], want["pattern"] = "string", d.pattern.String()
		}
		if d.enum != nil {
			want["enum"] = toAny(d.enum)
		}
		if d.notEnum != nil {
			want["not"] = map[string]any{"enum": toAny(d.notEnum)}
		}
		if d.maxLen > 0 {
			want["maxLength"] = float64(d.maxLen)
		}
		if got := withoutAnnotations(def); !reflect.DeepEqual(got, want) {
			t.Errorf("$defs/%s = %v, Go has %v", name, got, want)
		}
	}

	for typ, name := range defNames {
		covered[name] = true
		def, ok := defs[name].(map[string]any)
		if !ok {
			t.Errorf("%s has no $defs/%s", typ.Name(), name)
			continue
		}
		if def["type"] != "object" || def["additionalProperties"] != false {
			t.Errorf("$defs/%s must be a closed object", name)
		}
		props, _ := def["properties"].(map[string]any)
		var required, keys []string
		for _, f := range fieldsOf(typ) {
			keys = append(keys, f.name)
			if f.required {
				required = append(required, f.name)
			}
			prop, ok := props[f.name].(map[string]any)
			if !ok {
				t.Errorf("$defs/%s has no property %s", name, f.name)
				continue
			}
			if want, got := expectedProperty(t, f.typ, f.c), withoutAnnotations(prop); !reflect.DeepEqual(got, want) {
				t.Errorf("$defs/%s/properties/%s = %v, Go has %v", name, f.name, got, want)
			}
		}
		if got := sortedNames(props); !slices.Equal(got, sortedCopy(keys)) {
			t.Errorf("$defs/%s properties = %v, Go has %v", name, got, sortedCopy(keys))
		}
		var gotRequired []string
		for _, r := range asList(def["required"]) {
			gotRequired = append(gotRequired, r.(string))
		}
		if !slices.Equal(sortedCopy(gotRequired), sortedCopy(required)) {
			t.Errorf("$defs/%s required = %v, Go has %v", name, gotRequired, required)
		}
	}
	for name := range defs {
		if !covered[name] {
			t.Errorf("$defs/%s has no Go counterpart", name)
		}
	}
	var roots []string
	for _, r := range asList(doc["oneOf"]) {
		roots = append(roots, r.(map[string]any)["$ref"].(string))
	}
	if want := []string{"#/$defs/app", "#/$defs/provider", "#/$defs/platform", "#/$defs/toolsGenerated"}; !slices.Equal(roots, want) {
		t.Errorf("manifest oneOf = %v, want %v", roots, want)
	}
}

func TestBundleAndOverlaySchemas(t *testing.T) {
	manifest := loadSchemaDoc(t, schema.Manifest)
	bundle := loadSchemaDoc(t, schema.Bundle)
	base := manifest["$id"].(string)
	props := bundle["properties"].(map[string]any)
	for member, def := range map[string]string{"apps": "app", "providers": "provider", "platforms": "platform"} {
		got := props[member].(map[string]any)["items"].(map[string]any)["$ref"]
		if got != base+"#/$defs/"+def {
			t.Errorf("bundle %s items = %v, want the manifest's $defs/%s", member, got, def)
		}
	}
	overlay := loadSchemaDoc(t, schema.Overlay)
	op := overlay["properties"].(map[string]any)
	key := regexp.MustCompile(op["key"].(map[string]any)["pattern"].(string))
	for s, ok := range map[string]bool{"gmail/composio": true, "platform/arcade": true, "Gmail/composio": false, "gmail/": false, "platform/": false, "gmail/composio/x": false} {
		if key.MatchString(s) != ok {
			t.Errorf("overlay key %q: match = %v, want %v", s, !ok, ok)
		}
	}
	// priceCardRef points into tool-price-card-v1: the vendor and capability
	// patterns of internal/toolcatalog/card.go:120-121.
	ref := op["priceCardRef"].(map[string]any)["properties"].(map[string]any)
	if ref["vendor"].(map[string]any)["pattern"] != `^[a-z0-9][a-z0-9_-]{0,47}$` || ref["capability"].(map[string]any)["pattern"] != `^[a-z][a-z0-9_.]*$` {
		t.Errorf("priceCardRef patterns differ from tool-price-card-v1's")
	}
}

func toAny(list []string) []any {
	out := make([]any, len(list))
	for i, s := range list {
		out[i] = s
	}
	return out
}

func asList(v any) []any {
	list, _ := v.([]any)
	return list
}

func sortedCopy(list []string) []string {
	out := slices.Clone(list)
	slices.Sort(out)
	return out
}
