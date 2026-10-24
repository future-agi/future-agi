package reqopts

import (
	"bytes"
	"encoding/json"
	"errors"
	"slices"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// providerKeys are the keys of the OpenRouter provider object the gateway
// knows (tenant.ProviderPrefs). Any other key is a 400.
var providerKeys = map[string]bool{
	"order": true, "only": true, "ignore": true, "allow_fallbacks": true,
	"require_parameters": true, "data_collection": true, "zdr": true,
	"quantizations": true, "sort": true, "max_price": true,
	"preferred_max_latency": true, "preferred_min_throughput": true,
}

// quantizations are OpenRouter's quantization levels.
var quantizations = map[string]bool{
	"int4": true, "int8": true, "fp4": true, "fp6": true, "fp8": true,
	"fp16": true, "bf16": true, "fp32": true, "unknown": true,
}

// sortAliases maps every accepted sort value to its canonical name:
// OpenRouter's price, throughput and latency, and Vercel's cost, tps and
// ttft for the same orderings.
var sortAliases = map[string]string{
	"price": "price", "throughput": "throughput", "latency": "latency",
	"cost": "price", "tps": "throughput", "ttft": "latency",
}

// CanonicalSort returns the canonical sort for s (price, throughput or
// latency), accepting the aliases cost, tps and ttft.
func CanonicalSort(s string) (string, bool) {
	c, ok := sortAliases[strings.ToLower(strings.TrimSpace(s))]
	return c, ok
}

// parseProviderPrefs decodes the caller's provider object. Order, only and
// ignore name upstream provider slugs (see SlugMatches), never gateway
// provider ids. Sort values are stored in their canonical form.
func parseProviderPrefs(raw json.RawMessage) (*models.ProviderPrefs, *models.APIError) {
	const param = "provider"
	keys, apiErr := objectKeys(raw, param, CodeInvalidProviderPreference)
	if apiErr != nil {
		return nil, apiErr
	}
	for _, k := range keys {
		if !providerKeys[k] {
			return nil, fieldError(CodeInvalidProviderPreference, param+"."+k, "provider."+k+" is not a provider preference")
		}
	}
	// Decoded with the org config's own codecs (tenant.ProviderPrefs, strict
	// on every nested object) and copied into the models mirror.
	var w tenant.ProviderPrefs
	if err := decodeStrict(raw, &w); err != nil {
		return nil, decodeError(param, err)
	}
	p := &models.ProviderPrefs{
		AllowFallbacks:    w.AllowFallbacks,
		RequireParameters: w.RequireParameters,
		ZDR:               w.ZDR,
	}
	var e *models.APIError
	if p.Order, e = slugList(w.Order, param+".order"); e != nil {
		return nil, e
	}
	if p.Only, e = slugList(w.Only, param+".only"); e != nil {
		return nil, e
	}
	if p.Ignore, e = slugList(w.Ignore, param+".ignore"); e != nil {
		return nil, e
	}
	if slices.Contains(keys, "data_collection") {
		if w.DataCollection != "allow" && w.DataCollection != "deny" {
			return nil, fieldError(CodeInvalidProviderPreference, param+".data_collection", "provider.data_collection must be allow or deny")
		}
		p.DataCollection = w.DataCollection
	}
	for _, q := range w.Quantizations {
		if !quantizations[strings.ToLower(q)] {
			return nil, fieldError(CodeInvalidProviderPreference, param+".quantizations", "unknown quantization "+q)
		}
		p.Quantizations = append(p.Quantizations, strings.ToLower(q))
	}
	if p.Sort, e = checkSort(w.Sort, param+".sort"); e != nil {
		return nil, e
	}
	if p.MaxPrice, e = checkMaxPrice(w.MaxPrice, param+".max_price"); e != nil {
		return nil, e
	}
	if p.PreferredMaxLatency, e = checkBound(w.PreferredMaxLatency, param+".preferred_max_latency"); e != nil {
		return nil, e
	}
	if p.PreferredMinThroughput, e = checkBound(w.PreferredMinThroughput, param+".preferred_min_throughput"); e != nil {
		return nil, e
	}
	return p, nil
}

// ProviderObject is p in the OpenRouter wire shape (tenant.ProviderPrefs,
// whose JSON is OpenRouter's provider object), for re-emitting the caller's
// preferences to a BYOK OpenRouter upstream and for the request log. Sort is
// written in its canonical form.
func ProviderObject(p *models.ProviderPrefs) *tenant.ProviderPrefs {
	if p == nil {
		return nil
	}
	w := &tenant.ProviderPrefs{
		Order: p.Order, Only: p.Only, Ignore: p.Ignore,
		AllowFallbacks: p.AllowFallbacks, RequireParameters: p.RequireParameters,
		DataCollection: p.DataCollection, ZDR: p.ZDR, Quantizations: p.Quantizations,
	}
	if p.Sort != nil {
		w.Sort = &tenant.ProviderSort{By: p.Sort.By, Partition: p.Sort.Partition}
	}
	if mp := p.MaxPrice; mp != nil {
		w.MaxPrice = &tenant.ProviderMaxPrice{Prompt: mp.Prompt, Completion: mp.Completion, Request: mp.Request, Image: mp.Image}
	}
	w.PreferredMaxLatency = boundWire(p.PreferredMaxLatency)
	w.PreferredMinThroughput = boundWire(p.PreferredMinThroughput)
	return w
}

func boundWire(b *models.PercentileBound) *tenant.PercentileBound {
	if b == nil {
		return nil
	}
	return &tenant.PercentileBound{Value: b.Value, P50: b.P50, P75: b.P75, P90: b.P90, P99: b.P99}
}

// providerFields are the support-registry names of the provider keys the
// caller set.
func providerFields(p *models.ProviderPrefs) []string {
	if p == nil {
		return nil
	}
	var out []string
	add := func(set bool, f string) {
		if set {
			out = append(out, f)
		}
	}
	add(p.Order != nil, FieldProviderOrder)
	add(p.Only != nil, FieldProviderOnly)
	add(p.Ignore != nil, FieldProviderIgnore)
	add(p.AllowFallbacks != nil, FieldProviderAllowFallbacks)
	add(p.RequireParameters != nil, FieldProviderRequireParameters)
	add(p.DataCollection != "", FieldProviderDataCollection)
	add(p.ZDR != nil, FieldProviderZDR)
	add(p.Quantizations != nil, FieldProviderQuantizations)
	add(p.Sort != nil, FieldProviderSort)
	add(p.MaxPrice != nil, FieldProviderMaxPrice)
	add(p.PreferredMaxLatency != nil, FieldProviderPreferredMaxLatency)
	add(p.PreferredMinThroughput != nil, FieldProviderPreferredMinThroughput)
	return out
}

// checkSort validates the sort and stores its canonical name.
func checkSort(s *tenant.ProviderSort, param string) (*models.ProviderSort, *models.APIError) {
	if s == nil {
		return nil, nil
	}
	by, ok := CanonicalSort(s.By)
	if !ok {
		return nil, fieldError(CodeInvalidProviderPreference, param, param+" must be price, throughput or latency (or cost, tps, ttft)")
	}
	switch s.Partition {
	case "", "model", "none":
	default:
		return nil, fieldError(CodeInvalidProviderPreference, param+".partition", param+".partition must be model or none")
	}
	return &models.ProviderSort{By: by, Partition: s.Partition}, nil
}

// checkMaxPrice keeps OpenRouter's units: prompt and completion in USD per
// million tokens, request and image in USD per unit (see PerTokenMaxPrice).
func checkMaxPrice(mp *tenant.ProviderMaxPrice, param string) (*models.ProviderMaxPrice, *models.APIError) {
	if mp == nil {
		return nil, nil
	}
	for _, f := range []struct {
		name string
		v    *float64
	}{{"prompt", mp.Prompt}, {"completion", mp.Completion}, {"request", mp.Request}, {"image", mp.Image}} {
		if f.v != nil && *f.v < 0 {
			return nil, fieldError(CodeInvalidProviderPreference, param+"."+f.name, param+"."+f.name+" must not be negative")
		}
	}
	return &models.ProviderMaxPrice{Prompt: mp.Prompt, Completion: mp.Completion, Request: mp.Request, Image: mp.Image}, nil
}

// PerTokenMaxPrice converts max_price into the units of models.PriceCard:
// prompt and completion from USD per million tokens to USD per token;
// request and image stay USD per unit. A nil result means no cap.
func PerTokenMaxPrice(mp *models.ProviderMaxPrice) (prompt, completion, request, image *float64) {
	if mp == nil {
		return nil, nil, nil, nil
	}
	perToken := func(v *float64) *float64 {
		if v == nil {
			return nil
		}
		x := *v / 1e6
		return &x
	}
	return perToken(mp.Prompt), perToken(mp.Completion), mp.Request, mp.Image
}

// checkBound validates a latency or throughput preference: a number or
// {p50, p75, p90, p99}, none negative.
func checkBound(b *tenant.PercentileBound, param string) (*models.PercentileBound, *models.APIError) {
	if b == nil {
		return nil, nil
	}
	if b.Value == nil && b.P50 == nil && b.P75 == nil && b.P90 == nil && b.P99 == nil {
		return nil, fieldError(CodeInvalidProviderPreference, param, param+" needs a number or at least one of p50, p75, p90, p99")
	}
	for _, v := range []*float64{b.Value, b.P50, b.P75, b.P90, b.P99} {
		if v != nil && *v < 0 {
			return nil, fieldError(CodeInvalidProviderPreference, param, param+" must not be negative")
		}
	}
	return &models.PercentileBound{Value: b.Value, P50: b.P50, P75: b.P75, P90: b.P90, P99: b.P99}, nil
}

// slugList checks a list of upstream slugs: non-empty strings.
func slugList(in []string, param string) ([]string, *models.APIError) {
	if in == nil {
		return nil, nil
	}
	out := make([]string, 0, len(in))
	for _, s := range in {
		s = strings.TrimSpace(s)
		if s == "" {
			return nil, fieldError(CodeInvalidProviderPreference, param, param+" must not contain an empty provider slug")
		}
		out = append(out, s)
	}
	return out, nil
}

// objectKeys returns raw's keys, sorted, or a 400 when raw is not an object.
func objectKeys(raw json.RawMessage, param, code string) ([]string, *models.APIError) {
	var obj map[string]json.RawMessage
	if !isJSONObject(raw) || json.Unmarshal(raw, &obj) != nil {
		return nil, fieldError(code, param, param+" must be a JSON object")
	}
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys, nil
}

// decodeError is the 400 for an object under param that failed to decode:
// a value of the wrong type names its field, anything else names param.
func decodeError(param string, err error) *models.APIError {
	var typeErr *json.UnmarshalTypeError
	if errors.As(err, &typeErr) && typeErr.Field != "" {
		field := param + "." + typeErr.Field
		return fieldError(CodeInvalidProviderPreference, field, field+" has the wrong type (got a JSON "+typeErr.Value+")")
	}
	return fieldError(CodeInvalidProviderPreference, param, param+": "+err.Error())
}

// decodeStrict decodes raw into v and refuses keys v does not declare.
func decodeStrict(raw json.RawMessage, v any) error {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}
