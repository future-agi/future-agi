package reqopts

import (
	"bytes"
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Body keys the gateway owns. Parse removes each from the
// request, so none reaches an upstream unless the gateway rebuilds it.
const (
	keyProvider        = "provider"
	keyProviderOptions = "providerOptions"
	keyModels          = "models"
	keyFallbacks       = "fallbacks"
	keyRoute           = "route"
	keyPlugins         = "plugins"
	keyTransforms      = "transforms"
	keyPreset          = "preset"
	keySessionID       = "session_id"
	keyCaching         = "caching"
)

var routingKeys = [...]string{
	keyProvider, keyProviderOptions, keyModels, keyFallbacks, keyRoute,
	keyPlugins, keyTransforms, keyPreset, keySessionID, keyCaching,
}

const (
	maxModels    = 5 // body models[] and providerOptions.gateway.models
	maxFallbacks = 3 // Messages API fallbacks[]
)

// Result is what Parse took out of a request.
type Result struct {
	Options models.RoutingOptions
	// Interpreted names, in InterpretedFields order, every field the request
	// set that the gateway interprets: the fields the support gate checks.
	Interpreted []string
}

// HasRoutingFields reports whether extra holds a routing field or a literal
// extra_body. It allocates nothing, so a request without either costs a few
// map lookups.
func HasRoutingFields(extra map[string]json.RawMessage) bool {
	if len(extra) == 0 {
		return false
	}
	if _, ok := extra[extraBodyKey]; ok {
		return true
	}
	for _, k := range routingKeys {
		if _, ok := extra[k]; ok {
			return true
		}
	}
	return false
}

// Parse hoists a literal extra_body into req, then parses every routing
// field out of req.Extra and deletes it. A malformed field is a 400 naming
// it; req may be partly stripped then, which does not matter because the
// request ends there.
func Parse(req *models.ChatCompletionRequest) (Result, *models.APIError) {
	var res Result
	if !HasRoutingFields(req.Extra) {
		return res, nil
	}
	if e := hoistExtraBody(req); e != nil {
		return res, e
	}
	extra := req.Extra
	take := func(k string) (json.RawMessage, bool) {
		raw, ok := extra[k]
		delete(extra, k)
		if !ok || isNull(raw) {
			return nil, false
		}
		return raw, true
	}
	o := &res.Options
	var e *models.APIError
	if raw, ok := take(keyProvider); ok {
		if o.Provider, e = parseProviderPrefs(raw); e != nil {
			return res, e
		}
	}
	if raw, ok := take(keyProviderOptions); ok {
		if o.Gateway, e = parseProviderOptions(raw); e != nil {
			return res, e
		}
	}
	rawModels, hasModels := take(keyModels)
	rawFallbacks, hasFallbacks := take(keyFallbacks)
	switch {
	case hasModels && hasFallbacks:
		return res, fieldError(CodeInvalidRoutingField, keyFallbacks, "send either models or fallbacks, not both")
	case hasModels:
		if o.Models, e = parseModelList(rawModels, keyModels); e != nil {
			return res, e
		}
	case hasFallbacks:
		if o.Models, e = parseFallbacks(rawFallbacks); e != nil {
			return res, e
		}
	}
	if raw, ok := take(keyRoute); ok {
		if o.Route, e = stringField(raw, keyRoute); e != nil {
			return res, e
		}
	}
	if raw, ok := take(keyPlugins); ok {
		if !isJSONArray(raw) {
			return res, fieldError(CodeInvalidRoutingField, keyPlugins, "plugins must be an array")
		}
		o.Plugins = append(json.RawMessage(nil), raw...)
	}
	if raw, ok := take(keyTransforms); ok {
		var t []string
		if json.Unmarshal(raw, &t) != nil {
			return res, fieldError(CodeInvalidRoutingField, keyTransforms, "transforms must be an array of strings")
		}
		o.Transforms = append(json.RawMessage(nil), raw...)
	}
	if raw, ok := take(keyPreset); ok {
		if o.Preset, e = stringField(raw, keyPreset); e != nil {
			return res, e
		}
	}
	if raw, ok := take(keySessionID); ok {
		if o.SessionID, e = stringField(raw, keySessionID); e != nil {
			return res, e
		}
	}
	if raw, ok := take(keyCaching); ok {
		o.Caching = append(json.RawMessage(nil), raw...)
	}
	res.Interpreted = interpretedFields(o, hasFallbacks)
	return res, nil
}

// CheckSupported refuses the first interpreted field not registered with
// MarkSupported: 400 routing_field_not_yet_supported, naming it.
func CheckSupported(interpreted []string) *models.APIError {
	for _, f := range interpreted {
		if !Supported(f) {
			return fieldError(CodeNotYetSupported, f, f+" is not supported by this gateway")
		}
	}
	return nil
}

// interpretedFields lists the interpreted fields o carries, in
// InterpretedFields order.
func interpretedFields(o *models.RoutingOptions, fromFallbacks bool) []string {
	set := make(map[string]bool, 8)
	for _, f := range providerFields(o.Provider) {
		set[f] = true
	}
	for _, f := range gatewayFields(o.Gateway) {
		set[f] = true
	}
	if o.Models != nil {
		if fromFallbacks {
			set[FieldFallbacks] = true
		} else {
			set[FieldModels] = true
		}
	}
	set[FieldPreset] = o.Preset != ""
	set[FieldCaching] = o.Caching != nil
	var out []string
	for _, f := range InterpretedFields {
		if set[f] {
			out = append(out, f)
		}
	}
	return out
}

// parseModelList reads a list of model ids: non-empty strings, deduplicated
// in order, at most maxModels.
func parseModelList(raw json.RawMessage, param string) ([]string, *models.APIError) {
	var in []string
	if json.Unmarshal(raw, &in) != nil {
		return nil, fieldError(CodeInvalidRoutingField, param, param+" must be an array of model ids")
	}
	return modelList(in, param)
}

func modelList(in []string, param string) ([]string, *models.APIError) {
	out := make([]string, 0, len(in))
	seen := make(map[string]bool, len(in))
	for _, m := range in {
		m = strings.TrimSpace(m)
		if m == "" {
			return nil, fieldError(CodeInvalidRoutingField, param, param+" must not contain an empty model id")
		}
		if !seen[m] {
			seen[m] = true
			out = append(out, m)
		}
	}
	if len(out) > maxModels {
		return nil, fieldError(CodeInvalidRoutingField, param, param+" may list at most 5 models")
	}
	return out, nil
}

type fallbackWire struct {
	Model string `json:"model"`
}

// parseFallbacks reads the Messages API's fallbacks [{model}], at most
// maxFallbacks, into the same model chain as models[].
func parseFallbacks(raw json.RawMessage) ([]string, *models.APIError) {
	var items []json.RawMessage
	if json.Unmarshal(raw, &items) != nil {
		return nil, fieldError(CodeInvalidRoutingField, keyFallbacks, "fallbacks must be an array of {model}")
	}
	if len(items) > maxFallbacks {
		return nil, fieldError(CodeInvalidRoutingField, keyFallbacks, "fallbacks may list at most 3 models")
	}
	ids := make([]string, 0, len(items))
	for _, it := range items {
		var f fallbackWire
		if decodeStrict(it, &f) != nil {
			return nil, fieldError(CodeInvalidRoutingField, keyFallbacks, "each fallback must be {model}")
		}
		ids = append(ids, f.Model)
	}
	return modelList(ids, keyFallbacks)
}

func stringField(raw json.RawMessage, param string) (string, *models.APIError) {
	var s string
	if json.Unmarshal(raw, &s) != nil {
		return "", fieldError(CodeInvalidRoutingField, param, param+" must be a string")
	}
	return s, nil
}

func isNull(raw json.RawMessage) bool {
	return bytes.Equal(bytes.TrimSpace(raw), []byte("null"))
}

func isJSONArray(raw json.RawMessage) bool {
	t := bytes.TrimSpace(raw)
	return len(t) > 0 && t[0] == '['
}
