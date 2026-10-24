package middleware

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The documented request headers RequestID reads before any other reader
// sees the request: x-agentcc-config, the x-agentcc-guardrail-policy
// alias, x-agentcc-property-<name>, x-agentcc-session-name and -path, and
// the client's own x-agentcc-request-id. Each one becomes the header (or the
// x-agentcc-metadata entry) the gateway already reads, so it is checked,
// bounded and applied exactly as that header is, on every route that reads
// it.

// Canonical request header names: the request header map's keys are
// canonical, and canonicalizing a name per request would allocate.
const (
	hdrConfig               = "X-Agentcc-Config"
	hdrGuardrailPolicyAlias = "X-Agentcc-Guardrail-Policy"
	hdrGuardrailPolicy      = "X-Guardrail-Policy"
	hdrClientRequestID      = "X-Agentcc-Request-Id"
	hdrSessionName          = "X-Agentcc-Session-Name"
	hdrSessionPath          = "X-Agentcc-Session-Path"
	hdrMetadata             = "X-Agentcc-Metadata"
	hdrPropertyPrefix       = "X-Agentcc-Property-"

	// HeaderClientRequestID is the response header that echoes the client's
	// own x-agentcc-request-id; x-agentcc-request-id stays the gateway's.
	HeaderClientRequestID = "X-Agentcc-Client-Request-Id"

	// HeaderConfigIgnored names, sorted and comma-separated, what an
	// x-agentcc-config the gateway answered holds but does not read: a
	// GatewayConfig section ("retry", "fallback", ...) or a cache field
	// ("cache.strategy"). Absent when everything sent was applied.
	HeaderConfigIgnored = "X-Agentcc-Config-Ignored"
)

// The metadata keys the folded headers are recorded under.
const (
	MetadataClientRequestID = "client_request_id"
	MetadataSessionName     = "session_name"
	MetadataSessionPath     = "session_path"
)

// Bounds. A value over its bound is a 400 naming the header, never cut.
const (
	MaxAgentccConfigBytes = 16 << 10
	maxClientRequestIDLen = 255 // as x-agentcc-session-id
	maxSessionNameLen     = 255
	maxSessionPathLen     = 1024
	maxPropertyNameLen    = 64
	maxPropertyValueLen   = 1024
	maxConfigStringLen    = 1024
)

const (
	errCodeAgentccConfig = "invalid_agentcc_config"
	errCodeAgentccHeader = "invalid_agentcc_header"
)

// readAgentccHeaders expands and folds the documented request headers into
// the headers the gateway reads, in place, and returns the client's own
// request id (validated) for the response to echo and what x-agentcc-config
// holds that is not read (HeaderConfigIgnored's value). A request that sends
// none of them is left untouched and costs no allocation. A refusal (400)
// changes nothing.
func readAgentccHeaders(h http.Header) (clientRequestID, configIgnored string, apiErr *models.APIError) {
	if !hasAgentccRequestHeaders(h) {
		return "", "", nil
	}
	if vs, ok := h[hdrConfig]; ok {
		if len(vs) != 1 {
			return "", "", configError("is sent more than once; send one JSON object")
		}
		ignored, apiErr := expandAgentccConfig(h, vs[0])
		if apiErr != nil {
			return "", "", apiErr
		}
		configIgnored = ignored
	}
	// The documented alias of X-Guardrail-Policy (the mode header the
	// guardrail plugin reads on every route it checks). The canonical name
	// wins when both are sent.
	if vs, ok := h[hdrGuardrailPolicyAlias]; ok && len(vs) > 0 {
		if _, set := h[hdrGuardrailPolicy]; !set {
			h[hdrGuardrailPolicy] = []string{vs[0]}
		}
	}
	clientRequestID, apiErr = foldIntoMetadata(h)
	if apiErr != nil {
		return "", "", apiErr
	}
	return clientRequestID, configIgnored, nil
}

// hasAgentccRequestHeaders reports whether h carries any header
// readAgentccHeaders reads, without allocating.
func hasAgentccRequestHeaders(h http.Header) bool {
	for _, k := range [...]string{hdrConfig, hdrGuardrailPolicyAlias, hdrClientRequestID, hdrSessionName, hdrSessionPath} {
		if _, ok := h[k]; ok {
			return true
		}
	}
	for k := range h {
		if isPropertyHeader(k) {
			return true
		}
	}
	return false
}

func isPropertyHeader(k string) bool {
	return len(k) >= len(hdrPropertyPrefix) && strings.EqualFold(k[:len(hdrPropertyPrefix)], hdrPropertyPrefix)
}

// foldIntoMetadata records the client's request id, the session name and
// path and every x-agentcc-property-<name> as entries of the
// x-agentcc-metadata header, which every route reads through the caller
// metadata path and its blocklist of gateway-owned keys (so a property can
// never set org_id, key_type, cost, a budget or rate-limit key, ...). On a
// conflict a dedicated header (x-agentcc-request-id, -session-name,
// -session-path) wins over a property of the same key, so the logged
// client_request_id is always the echoed one, and both win over the same
// key in x-agentcc-metadata, as that header wins over the body's metadata.
// x-agentcc-property-env: prod is "env": "prod" in metadata.
func foldIntoMetadata(h http.Header) (string, *models.APIError) {
	var add map[string]string
	put := func(key, value string) {
		if add == nil {
			add = make(map[string]string, 4)
		}
		add[key] = value
	}
	clientID, apiErr := boundedHeader(h, hdrClientRequestID, "x-agentcc-request-id", maxClientRequestIDLen)
	if apiErr != nil {
		return "", apiErr
	}
	name, apiErr := boundedHeader(h, hdrSessionName, "x-agentcc-session-name", maxSessionNameLen)
	if apiErr != nil {
		return "", apiErr
	}
	path, apiErr := boundedHeader(h, hdrSessionPath, "x-agentcc-session-path", maxSessionPathLen)
	if apiErr != nil {
		return "", apiErr
	}
	for k, vs := range h {
		if !isPropertyHeader(k) || len(vs) == 0 {
			continue
		}
		prop := strings.ToLower(k[len(hdrPropertyPrefix):])
		header := "x-agentcc-property-" + prop
		if prop == "" || len(prop) > maxPropertyNameLen {
			return "", headerError(header, fmt.Sprintf("needs a property name of 1 to %d characters after x-agentcc-property-", maxPropertyNameLen))
		}
		if len(vs[0]) > maxPropertyValueLen {
			return "", headerError(header, fmt.Sprintf("is longer than %d characters", maxPropertyValueLen))
		}
		if !validHeaderText(vs[0]) {
			return "", headerError(header, "contains a control character")
		}
		put(prop, vs[0])
	}
	// The dedicated headers last: they win over a property of their key.
	if clientID != "" {
		put(MetadataClientRequestID, clientID)
	}
	if name != "" {
		put(MetadataSessionName, name)
	}
	if path != "" {
		put(MetadataSessionPath, path)
	}
	if len(add) == 0 {
		return clientID, nil
	}
	// The caller's own metadata object, when it is one; any other value is
	// ignored by every reader.
	obj := map[string]json.RawMessage{}
	if vs := h[hdrMetadata]; len(vs) > 0 {
		var cur map[string]json.RawMessage
		if json.Unmarshal([]byte(vs[0]), &cur) == nil && cur != nil {
			obj = cur
		}
	}
	for k, v := range add {
		b, _ := json.Marshal(v)
		obj[k] = b
	}
	out, err := json.Marshal(obj)
	if err != nil {
		return "", headerError("x-agentcc-metadata", "could not be combined with the property headers: "+err.Error())
	}
	h[hdrMetadata] = []string{string(out)}
	return clientID, nil
}

// boundedHeader is h's value for canonical, refused (naming header) when it
// is longer than max characters or holds a control character.
func boundedHeader(h http.Header, canonical, header string, max int) (string, *models.APIError) {
	vs := h[canonical]
	if len(vs) == 0 || vs[0] == "" {
		return "", nil
	}
	if len(vs[0]) > max {
		return "", headerError(header, fmt.Sprintf("is longer than %d characters", max))
	}
	if !validHeaderText(vs[0]) {
		return "", headerError(header, "contains a control character")
	}
	return vs[0], nil
}

func headerError(header, reason string) *models.APIError {
	param := header
	e := models.ErrBadRequest(errCodeAgentccHeader, header+" "+reason)
	e.Param = &param
	return e
}

func configError(reason string) *models.APIError {
	param := "x-agentcc-config"
	e := models.ErrBadRequest(errCodeAgentccConfig, "x-agentcc-config "+reason)
	e.Param = &param
	return e
}

// validHeaderText reports whether s holds no control character other than
// a tab.
func validHeaderText(s string) bool {
	for i := 0; i < len(s); i++ {
		if c := s[i]; (c < 0x20 && c != '\t') || c == 0x7f {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------------
// x-agentcc-config
// ---------------------------------------------------------------------------

// configKind is how a config key's JSON value becomes its header's text.
type configKind uint8

const (
	kindString   configKind = iota // a non-empty JSON string
	kindScalar                     // a string or a number; its header's own reader checks it
	kindProperty                   // a string, a number or a bool
	kindInt                        // a whole number from min (a number or a string of digits)
	kindDuration                   // a Go duration string above zero ("30s")
	kindTTL                        // whole seconds from 0, or a Go duration string
	kindBool                       // true or false; false asks for nothing
	kindObject                     // a JSON object (or a string holding one)
)

type configKey struct {
	kind configKind
	min  int64
}

// agentccConfigKeys are the keys x-agentcc-config accepts: each is a
// documented per-request header's name without "x-agentcc-", and its value
// becomes that header (x-agentcc-<key>) unless the request sends the header
// itself. Beside them it reads only the SDK's GatewayConfig sections
// (gatewayConfigSections). Nothing else is accepted: not the server's own
// headers, not the runtime token's org-id or deployment-id, not async (a
// different API shape), not Authorization or a standard HTTP header.
var agentccConfigKeys = func() map[string]configKey {
	m := map[string]configKey{
		// Tracking and correlation.
		"trace-id":     {kind: kindString},
		"session-id":   {kind: kindString},
		"session-name": {kind: kindString},
		"session-path": {kind: kindString},
		"request-id":   {kind: kindString},
		"user-id":      {kind: kindString},
		// Metadata (each property is "property-<name>").
		"metadata": {kind: kindObject},
		// Cache control.
		"cache-ttl":           {kind: kindTTL},
		"cache-namespace":     {kind: kindString},
		"cache-force-refresh": {kind: kindBool},
		// Routing control.
		"provider-lock":       {kind: kindString},
		"complexity-override": {kind: kindString},
		"auto-preference":     {kind: kindString},
		// Guardrails: the mode, as X-Guardrail-Policy.
		"guardrail-policy": {kind: kindString},
		// Timeouts and retries.
		"timeout":         {kind: kindDuration},
		"request-timeout": {kind: kindInt, min: 1},
		"max-attempts":    {kind: kindScalar},
		"retry-delay-ms":  {kind: kindScalar},
		"backoff":         {kind: kindString},
		// Logging.
		"log-content": {kind: kindString},
	}
	// The agent context headers (x-agentcc-<field>); their own reader
	// checks each value with its own 400.
	for _, s := range models.AgentFieldSuffixes {
		if _, ok := m[s]; !ok {
			m[s] = configKey{kind: kindScalar}
		}
	}
	m["ledger-id"] = configKey{kind: kindScalar}
	return m
}()

// gatewayConfigSections are the GatewayConfig sections the SDK's
// to_headers() nests in x-agentcc-config (concepts/configuration.mdx), and
// loadBalance, the TypeScript client's spelling of load_balance. A section
// is a JSON object or null. The cache fields that have a header of their own
// (cacheSectionFields) are applied as that header; every other non-null
// field of cache, and every other section that sets anything, is not read
// and is named in HeaderConfigIgnored, so such a request is answered 200 and
// the caller can see what was skipped.
// "timeout" is also the flat x-agentcc-timeout key: a string is that
// duration; a number, whose unit is not known, is named, never applied.
var gatewayConfigSections = map[string]bool{
	"cache": true, "retry": true, "fallback": true, "load_balance": true, "loadBalance": true,
	"guardrails": true, "routing": true, "mirror": true, "timeout": true,
}

// cacheSectionFields are the cache section's fields read, each as the flat
// config key (and so the header) it stands for; forceRefresh is the
// TypeScript client's spelling.
var cacheSectionFields = map[string]string{
	"ttl":           "cache-ttl",
	"namespace":     "cache-namespace",
	"force_refresh": "cache-force-refresh",
	"forceRefresh":  "cache-force-refresh",
}

// Bounds of what HeaderConfigIgnored can name: a response header that grew
// with the request could be refused by a proxy in front of the gateway.
const (
	maxIgnoredNames     = 32
	maxSectionFieldName = 64
)

// configEntry is one header the config sets.
type configEntry struct {
	canonical string
	value     string
}

// configExpansion collects what one x-agentcc-config sets and names.
type configExpansion struct {
	entries []configEntry
	ignored []string
	// setBy is the config key (or section field, "cache.ttl") that sets
	// each header, so two that set the same one are refused.
	setBy map[string]string
}

// claim records that source sets the header canonical, refused when another
// key of the config already does.
func (c *configExpansion) claim(canonical, source string) *models.APIError {
	if prev, dup := c.setBy[canonical]; dup {
		return configError(fmt.Sprintf("keys %q and %q both set %s; send one", prev, source, strings.ToLower(canonical)))
	}
	c.setBy[canonical] = source
	return nil
}

// expandAgentccConfig parses raw, the x-agentcc-config header, and sets
// each header it names that the request does not send itself. It returns
// what the config holds that is not read (HeaderConfigIgnored's value, ""
// for nothing). Every key is checked before any header is set, so a refusal
// changes nothing.
func expandAgentccConfig(h http.Header, raw string) (string, *models.APIError) {
	if len(raw) > MaxAgentccConfigBytes {
		return "", configError(fmt.Sprintf("is larger than 16 KiB (%d bytes)", len(raw)))
	}
	dec := json.NewDecoder(strings.NewReader(raw))
	dec.UseNumber()
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return "", configError("must be a JSON object whose keys are request header names without \"x-agentcc-\" (for example {\"cache-ttl\":300})")
	}
	seen := make(map[string]bool, 8)
	c := configExpansion{entries: make([]configEntry, 0, 8), setBy: make(map[string]string, 8)}
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return "", configError("is not valid JSON: " + err.Error())
		}
		key, _ := tok.(string)
		if seen[key] {
			return "", configError(fmt.Sprintf("sets the key %q more than once", key))
		}
		seen[key] = true
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return "", configError("is not valid JSON: " + err.Error())
		}
		if gatewayConfigSections[key] && !(key == "timeout" && jsonKind(value) == '"') {
			if apiErr := c.section(key, value); apiErr != nil {
				return "", apiErr
			}
			continue
		}
		e, ok, apiErr := configHeader(key, value)
		if apiErr != nil {
			return "", apiErr
		}
		if apiErr := c.claim(http.CanonicalHeaderKey("x-agentcc-"+key), key); apiErr != nil {
			return "", apiErr
		}
		if ok {
			c.entries = append(c.entries, e)
		}
	}
	if tok, err := dec.Token(); err != nil || tok != json.Delim('}') {
		return "", configError("is not valid JSON")
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return "", configError("has data after its JSON object")
	}
	if len(c.ignored) > maxIgnoredNames {
		return "", configError(fmt.Sprintf("holds more than %d sections and cache fields the gateway does not read", maxIgnoredNames))
	}
	for _, e := range c.entries {
		// An explicit header wins.
		if _, ok := h[e.canonical]; !ok {
			h[e.canonical] = []string{e.value}
		}
	}
	if len(c.ignored) == 0 {
		return "", nil
	}
	sort.Strings(c.ignored)
	return strings.Join(c.ignored, ","), nil
}

// section reads the GatewayConfig section key: null asks for nothing; the
// cache object's fields are read (cacheSection); any other object that sets
// anything is named as not read, as is a numeric timeout. Anything else is
// refused.
func (c *configExpansion) section(key string, raw json.RawMessage) *models.APIError {
	switch jsonKind(raw) {
	case 'n':
		return nil
	case '{':
		if key == "cache" {
			return c.cacheSection(raw)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(raw, &fields); err != nil {
			return configError(fmt.Sprintf("key %q is not a valid JSON object", key))
		}
		for _, v := range fields {
			if jsonKind(v) != 'n' {
				c.ignored = append(c.ignored, key)
				break
			}
		}
		return nil
	case '0':
		if key == "timeout" {
			c.ignored = append(c.ignored, key)
			return nil
		}
	}
	if key == "timeout" {
		return configError(`key "timeout" must be a duration string such as "30s" (x-agentcc-timeout) or a GatewayConfig timeout object`)
	}
	return configError(fmt.Sprintf("key %q is a GatewayConfig section: send a JSON object (or null)", key))
}

// cacheSection reads the cache section's fields: ttl, namespace and
// force_refresh as the flat cache keys (each checked as that key is); every
// other non-null field is named as not read.
func (c *configExpansion) cacheSection(raw json.RawMessage) *models.APIError {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return configError(`key "cache" is not a valid JSON object`)
	}
	seen := make(map[string]bool, 4)
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return configError(`key "cache" is not a valid JSON object`)
		}
		field, _ := tok.(string)
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return configError(`key "cache" is not a valid JSON object`)
		}
		if field == "" || len(field) > maxSectionFieldName || !validSectionFieldName(field) {
			return configError(fmt.Sprintf(`key "cache" has a field name that is not 1 to %d letters, digits, '_', '-' or '.'`, maxSectionFieldName))
		}
		source := "cache." + field
		if seen[field] {
			return configError(fmt.Sprintf("sets the key %q more than once", source))
		}
		seen[field] = true
		if jsonKind(value) == 'n' {
			continue
		}
		flat, read := cacheSectionFields[field]
		if !read {
			c.ignored = append(c.ignored, source)
			continue
		}
		e, ok, apiErr := configKeyHeader(source, flat, value)
		if apiErr != nil {
			return apiErr
		}
		if apiErr := c.claim(http.CanonicalHeaderKey("x-agentcc-"+flat), source); apiErr != nil {
			return apiErr
		}
		if ok {
			c.entries = append(c.entries, e)
		}
	}
	return nil
}

// jsonKind is the kind of the JSON value raw: 'n' null, '{' object, '['
// array, '"' string, 't' or 'f' a bool, '0' a number.
func jsonKind(raw json.RawMessage) byte {
	b := bytes.TrimLeft(raw, " \t\r\n")
	if len(b) == 0 {
		return 0
	}
	switch c := b[0]; {
	case c == '-' || c >= '0' && c <= '9':
		return '0'
	default:
		return c
	}
}

func validSectionFieldName(s string) bool {
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' || c == '.') {
			return false
		}
	}
	return true
}

// configHeader is the header key's value sets, or ok false when the value
// asks for nothing (cache-force-refresh false).
func configHeader(key string, raw json.RawMessage) (configEntry, bool, *models.APIError) {
	if _, known := agentccConfigKeys[key]; !known {
		if prop, isProp := strings.CutPrefix(key, "property-"); isProp {
			if prop == "" || len(prop) > maxPropertyNameLen || prop != strings.ToLower(prop) || !validPropertyName(prop) {
				return configEntry{}, false, configError(fmt.Sprintf("key %q needs a lower-case property name of 1 to %d letters, digits, '-', '_' or '.'", key, maxPropertyNameLen))
			}
			return configValueHeader(key, key, configKey{kind: kindProperty}, raw)
		}
		return configEntry{}, false, configError(fmt.Sprintf("has the unknown key %q: its keys are the request header names without \"x-agentcc-\", in lower case, and the GatewayConfig sections (cache, retry, fallback, load_balance, guardrails, routing, mirror, timeout)", key))
	}
	return configKeyHeader(key, key, raw)
}

// configKeyHeader is the header the known config key flat sets from raw;
// source names the value in a refusal ("cache-ttl", or "cache.ttl" for a
// cache section field).
func configKeyHeader(source, flat string, raw json.RawMessage) (configEntry, bool, *models.APIError) {
	return configValueHeader(source, flat, agentccConfigKeys[flat], raw)
}

func configValueHeader(source, flat string, spec configKey, raw json.RawMessage) (configEntry, bool, *models.APIError) {
	value, ok, reason := configValue(spec, raw)
	if reason != "" {
		return configEntry{}, false, configError(fmt.Sprintf("key %q %s", source, reason))
	}
	if !ok {
		return configEntry{}, false, nil
	}
	if len(value) > maxConfigStringLen && spec.kind != kindObject {
		return configEntry{}, false, configError(fmt.Sprintf("key %q is longer than %d characters", source, maxConfigStringLen))
	}
	if !validHeaderText(value) {
		return configEntry{}, false, configError(fmt.Sprintf("key %q contains a control character", source))
	}
	return configEntry{canonical: http.CanonicalHeaderKey("x-agentcc-" + flat), value: value}, true, nil
}

// configValue is raw as its header's text under spec, ok false when it asks
// for nothing, or the reason it is refused.
func configValue(spec configKey, raw json.RawMessage) (value string, ok bool, reason string) {
	var v any
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		return "", false, "is not valid JSON"
	}
	switch spec.kind {
	case kindString:
		s, isStr := v.(string)
		if !isStr || s == "" {
			return "", false, "must be a non-empty string"
		}
		return s, true, ""
	case kindScalar, kindProperty:
		switch t := v.(type) {
		case string:
			if t == "" {
				return "", false, "must not be empty"
			}
			return t, true, ""
		case json.Number:
			return t.String(), true, ""
		case bool:
			if spec.kind == kindProperty {
				return strconv.FormatBool(t), true, ""
			}
		}
		if spec.kind == kindProperty {
			return "", false, "must be a string, a number or true/false"
		}
		return "", false, "must be a string or a number"
	case kindInt:
		n, isInt := wholeNumber(v)
		if !isInt || n < spec.min {
			return "", false, fmt.Sprintf("must be a whole number from %d", spec.min)
		}
		return strconv.FormatInt(n, 10), true, ""
	case kindDuration:
		s, isStr := v.(string)
		if !isStr {
			return "", false, `must be a duration string such as "30s" or "500ms" (for milliseconds use "request-timeout")`
		}
		if d, err := time.ParseDuration(s); err != nil || d <= 0 {
			return "", false, `must be a duration above zero such as "30s" or "500ms"`
		}
		return s, true, ""
	case kindTTL:
		if n, isInt := wholeNumber(v); isInt && n >= 0 {
			return strconv.FormatInt(n, 10), true, ""
		}
		if s, isStr := v.(string); isStr {
			if d, err := time.ParseDuration(s); err == nil && d >= 0 {
				return s, true, ""
			}
		}
		return "", false, `must be whole seconds from 0 or a duration string such as "5m"`
	case kindBool:
		b, isBool := v.(bool)
		if !isBool {
			return "", false, "must be true or false"
		}
		if !b {
			return "", false, ""
		}
		return "true", true, ""
	case kindObject:
		if s, isStr := v.(string); isStr {
			if !isJSONObjectText([]byte(s)) {
				return "", false, "must be a JSON object"
			}
			return s, true, ""
		}
		if _, isObj := v.(map[string]any); !isObj {
			return "", false, "must be a JSON object"
		}
		var compact bytes.Buffer
		if err := json.Compact(&compact, raw); err != nil {
			return "", false, "must be a JSON object"
		}
		return compact.String(), true, ""
	}
	return "", false, "is not accepted"
}

// wholeNumber is v as an integer when it is a JSON number whose value is
// exactly whole (300, 300.0, 3e2; a Python float such as ttl=300.0 arrives
// as 300.0) or a string of decimal digits.
func wholeNumber(v any) (int64, bool) {
	switch t := v.(type) {
	case json.Number:
		if n, err := strconv.ParseInt(t.String(), 10, 64); err == nil {
			return n, true
		}
		return exactWholeNumber(t.String())
	case string:
		n, err := strconv.ParseInt(t, 10, 64)
		return n, err == nil
	}
	return 0, false
}

// exactWholeNumber is the JSON number text s as an int64 when its value is
// exactly whole and fits. It reads the decimal digits themselves, never a
// float64 (which would turn 3.0000000000000000001 into 3) and never a big
// number built from the exponent (whose cost grows with it).
func exactWholeNumber(s string) (int64, bool) {
	neg := strings.HasPrefix(s, "-")
	s = strings.TrimPrefix(s, "-")
	mantissa, exp := s, 0
	if i := strings.IndexAny(s, "eE"); i >= 0 {
		// The config is at most 16 KiB, so with an exponent past +-2^20 a
		// nonzero value is a fraction or past int64 (a zero one such as
		// 0e9999999 is refused too); the bound keeps the point arithmetic
		// below far from overflow.
		e, err := strconv.Atoi(s[i+1:])
		if err != nil || e > 1<<20 || e < -(1<<20) {
			return 0, false
		}
		mantissa, exp = s[:i], e
	}
	intPart, fracPart, _ := strings.Cut(mantissa, ".")
	digits := intPart + fracPart
	if digits == "" || strings.Trim(digits, "0123456789") != "" {
		return 0, false
	}
	// The value is 0.<digits> x 10^point once leading zeros are gone.
	trimmed := strings.TrimLeft(digits, "0")
	point := len(intPart) + exp - (len(digits) - len(trimmed))
	trimmed = strings.TrimRight(trimmed, "0")
	if trimmed == "" {
		return 0, true
	}
	// A digit right of the point is a fraction; more than 19 digits left of
	// it does not fit an int64.
	if len(trimmed) > point || point > 19 {
		return 0, false
	}
	text := trimmed + strings.Repeat("0", point-len(trimmed))
	if neg {
		text = "-" + text
	}
	n, err := strconv.ParseInt(text, 10, 64)
	if err != nil {
		return 0, false
	}
	return n, true
}

func isJSONObjectText(b []byte) bool {
	var m map[string]json.RawMessage
	return json.Unmarshal(b, &m) == nil && m != nil
}

func validPropertyName(s string) bool {
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '-' || c == '_' || c == '.') {
			return false
		}
	}
	return true
}
