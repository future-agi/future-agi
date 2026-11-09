package overlay

import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"slices"
	"strings"
	"unicode/utf8"

	"gopkg.in/yaml.v3"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
)

// decodeEntry holds a decoded document to connector-price-overlay/v1.
func decodeEntry(v any) (*Entry, []error) {
	var errs []error
	fail := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }
	doc, ok := v.(canonjson.Object)
	if !ok {
		return nil, []error{errors.New("must be an object")}
	}
	if !onlyKeys(doc, "/", []string{"schema", "key", "estimate", "billingMode", "priceCardRef"}, []string{"schema", "key", "estimate", "billingMode"}, fail) {
		return nil, errs
	}
	if s, _ := get(doc, "schema").(string); s != Schema {
		fail("/schema: must be %s", Schema)
	}
	e := &Entry{}
	e.Key, _ = get(doc, "key").(string)
	if !keyPattern.MatchString(e.Key) {
		fail("/key: must be <app-id>/<provider-id> or platform/<platform-id>")
	}
	e.BillingMode, _ = get(doc, "billingMode").(string)
	if e.BillingMode != "byok" && e.BillingMode != "managed" {
		fail("/billingMode: must be byok or managed")
	}
	estimates, ok := get(doc, "estimate").([]any)
	if !ok || len(estimates) < 1 || len(estimates) > maxEstimates {
		fail("/estimate: must hold 1 to %d estimates", maxEstimates)
	}
	for i, raw := range estimates {
		if est, ok := decodeEstimate(fmt.Sprintf("/estimate/%d", i), raw, fail); ok {
			e.Estimate = append(e.Estimate, est)
		}
	}
	ref, hasRef := doc.Get("priceCardRef")
	switch {
	case e.BillingMode == "managed" && !hasRef:
		fail("/priceCardRef: is required when billingMode is managed: a managed call settles at a tool-price-card-v1 card")
	case e.BillingMode != "managed" && hasRef:
		fail("/priceCardRef: is allowed only when billingMode is managed")
	case hasRef:
		e.PriceCardRef = decodeRef(ref, fail)
	}
	if len(errs) > 0 {
		return nil, errs
	}
	return e, nil
}

func decodeEstimate(at string, raw any, fail func(string, ...any)) (Estimate, bool) {
	obj, ok := raw.(canonjson.Object)
	if !ok {
		fail("%s: must be an object", at)
		return Estimate{}, false
	}
	keys := []string{"unit", "usdPerUnit", "source", "checkedAt"}
	if !onlyKeys(obj, at, keys, keys, fail) {
		return Estimate{}, false
	}
	est := Estimate{}
	est.Unit, _ = get(obj, "unit").(string)
	if !unitPattern.MatchString(est.Unit) {
		fail("%s/unit: must match %s", at, unitPattern)
	}
	switch n := get(obj, "usdPerUnit").(type) {
	case int64:
		est.USDPerUnit = float64(n)
	case float64:
		est.USDPerUnit = n
	default:
		fail("%s/usdPerUnit: must be a number", at)
	}
	if est.USDPerUnit < 0 {
		fail("%s/usdPerUnit: must not be negative", at)
	}
	est.Source, _ = get(obj, "source").(string)
	if utf8.RuneCountInString(est.Source) > maxSource || !sourcePattern.MatchString(est.Source) {
		fail("%s/source: must be an https URL of at most %d characters", at, maxSource)
	}
	est.CheckedAt, _ = get(obj, "checkedAt").(string)
	if !checkedDate(est.CheckedAt) {
		fail("%s/checkedAt: must be a date, YYYY-MM-DD", at)
	}
	return est, true
}

func decodeRef(raw any, fail func(string, ...any)) *PriceCardRef {
	obj, ok := raw.(canonjson.Object)
	keys := []string{"capability", "vendor", "sku"}
	if !ok {
		fail("/priceCardRef: must be an object")
		return nil
	}
	if !onlyKeys(obj, "/priceCardRef", keys, keys, fail) {
		return nil
	}
	ref := &PriceCardRef{}
	ref.Capability, _ = get(obj, "capability").(string)
	ref.Vendor, _ = get(obj, "vendor").(string)
	ref.SKU, _ = get(obj, "sku").(string)
	if !capabilityPattern.MatchString(ref.Capability) {
		fail("/priceCardRef/capability: must match %s", capabilityPattern)
	}
	if !vendorPattern.MatchString(ref.Vendor) {
		fail("/priceCardRef/vendor: must match %s", vendorPattern)
	}
	if n := utf8.RuneCountInString(ref.SKU); n < 1 || n > maxSKU {
		fail("/priceCardRef/sku: must be 1 to %d characters", maxSKU)
	}
	return ref
}

// onlyKeys refuses a member outside allowed, a missing required member and
// a null, and reports whether the object passed.
func onlyKeys(obj canonjson.Object, at string, allowed, required []string, fail func(string, ...any)) bool {
	ok := true
	for _, m := range obj {
		switch {
		case !slices.Contains(allowed, m.Key):
			fail("%s: member %q is not in %s", strings.TrimSuffix(at, "/"), m.Key, Schema)
			ok = false
		case m.Value == nil:
			fail("%s/%s: must not be null", strings.TrimSuffix(at, "/"), m.Key)
			ok = false
		}
	}
	for _, k := range required {
		if _, present := obj.Get(k); !present {
			fail("%s: has no %s", strings.TrimSuffix(at, "/"), k)
			ok = false
		}
	}
	return ok
}

func get(o canonjson.Object, key string) any {
	v, _ := o.Get(key)
	return v
}

// FindDocuments returns every file in fsys holding an overlay document: a
// JSON or YAML document (any document of a multi-document YAML file) whose
// top-level schema member is connector-price-overlay/v1, whatever the
// file's name. A file that does not parse is not one; the loader that owns
// the file reports it.
func FindDocuments(fsys fs.FS) ([]string, error) {
	var found []string
	err := fs.WalkDir(fsys, ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
		data, err := fs.ReadFile(fsys, p)
		if err != nil {
			return err
		}
		if holdsOverlay(data) {
			found = append(found, p)
		}
		return nil
	})
	return found, err
}

func holdsOverlay(data []byte) bool {
	if v, err := canonjson.Parse(data); err == nil {
		obj, _ := v.(canonjson.Object)
		s, _ := get(obj, "schema").(string)
		return s == Schema
	}
	dec := yaml.NewDecoder(bytes.NewReader(data))
	for {
		var doc yaml.Node
		if err := dec.Decode(&doc); err != nil {
			return false // the end, or not YAML
		}
		if len(doc.Content) != 1 || doc.Content[0].Kind != yaml.MappingNode {
			continue
		}
		m := doc.Content[0].Content
		for i := 0; i+1 < len(m); i += 2 {
			if m[i].Value == "schema" && m[i+1].Kind == yaml.ScalarNode && m[i+1].Value == Schema {
				return true
			}
		}
	}
}
