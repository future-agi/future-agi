package otel

import (
	"encoding/json"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
)

// Mirrors OpenAI's own metadata limits. Applied at export, not at parse: the
// request log keeps everything the caller sent.
const (
	maxMetadataPairs    = 16
	maxMetadataKeyLen   = 64
	maxMetadataValueLen = 512
)

// Namespaced so a caller key cannot shadow a gen_ai.* convention.
const metadataAttrPrefix = "agentcc.metadata."

// Counts keys the caps removed. Outside the namespace so it cannot collide
// with a caller key; absent when nothing was dropped.
const metadataDroppedAttr = "agentcc.metadata_dropped"

// attachMetadata emits caller dimensions twice: the `metadata` object platform
// ingest parses, and flattened copies that are queryable. Not redacted — these
// are grouping keys.
func (p *Plugin) attachMetadata(span *otelpkg.Span, rc *models.RequestContext) {
	if len(rc.CustomMetadataKeys) == 0 {
		return
	}

	// Sorted: ranging the map would make truncation follow randomised hash
	// order, so a dimension would appear and vanish between identical requests.
	keys := append([]string(nil), rc.CustomMetadataKeys...)
	sort.Strings(keys)

	custom := make(map[string]string, len(keys))
	dropped := 0
	for _, k := range keys {
		v, ok := rc.Metadata[k]
		if !ok {
			continue
		}
		if _, seen := custom[k]; seen {
			continue
		}
		// A truncated key is a different key, not a shorter one — drop it.
		if len(k) > maxMetadataKeyLen || len(custom) >= maxMetadataPairs {
			dropped++
			continue
		}
		custom[k] = truncateAtRune(v, maxMetadataValueLen)
	}
	if len(custom) == 0 {
		return
	}

	if encoded, err := json.Marshal(custom); err == nil {
		span.SetAttribute("metadata", string(encoded))
	}
	if dropped > 0 {
		span.SetAttribute(metadataDroppedAttr, dropped)
	}
	if !p.metadataAttributes {
		return
	}
	for k, v := range custom {
		span.SetAttribute(metadataAttrPrefix+k, v)
	}
}

// A wildcard is written once and then matches whatever a proxy adds upstream,
// so the count is bounded too.
const (
	maxCapturedHeaders   = 24
	maxHeaderValueLen    = 512
	headerAttrPrefix     = "http.request.header."
	headerValueSeparator = ","
)

// Never exported, whatever the allowlist says. cloneRequestHeaders copies
// x-api-key into Authorization, so a live credential is always present and a
// pattern as ordinary as "x-*" would ship it to the trace backend.
var headerDenylist = map[string]struct{}{
	"authorization":        {},
	"proxy-authorization":  {},
	"www-authenticate":     {},
	"cookie":               {},
	"set-cookie":           {},
	"api-key":              {},
	"x-api-key":            {},
	"x-goog-api-key":       {},
	"x-amz-security-token": {},
}

// Allowlist only: a blocklist complete enough for the alternative is one you
// can only be wrong about once.
type headerMatcher struct {
	exact  map[string]struct{}
	prefix []string
}

// newHeaderMatcher returns nil when nothing usable was configured, which is
// the capture-nothing default.
func newHeaderMatcher(patterns []string) *headerMatcher {
	m := &headerMatcher{exact: make(map[string]struct{}, len(patterns))}
	for _, pat := range patterns {
		pat = strings.ToLower(strings.TrimSpace(pat))
		if pat == "" {
			continue
		}
		if strings.HasSuffix(pat, "*") {
			m.prefix = append(m.prefix, strings.TrimSuffix(pat, "*"))
			continue
		}
		m.exact[pat] = struct{}{}
	}
	if len(m.exact) == 0 && len(m.prefix) == 0 {
		return nil
	}
	return m
}

// match reports whether a lower-cased header name may be exported.
func (m *headerMatcher) match(name string) bool {
	if _, denied := headerDenylist[name]; denied {
		return false
	}
	if _, ok := m.exact[name]; ok {
		return true
	}
	for _, pre := range m.prefix {
		if strings.HasPrefix(name, pre) {
			return true
		}
	}
	return false
}

// attachRequestHeaders copies allowlisted headers as http.request.header.<name>.
// Joined string, not semconv's array: arrays land in the non-queryable
// attributes_extra. Redacted, unlike metadata.
func (p *Plugin) attachRequestHeaders(span *otelpkg.Span, rc *models.RequestContext) {
	if p.captureHeaders == nil || len(rc.RequestHeaders) == 0 {
		return
	}

	names := make([]string, 0, len(rc.RequestHeaders))
	for name := range rc.RequestHeaders {
		lower := strings.ToLower(name)
		if p.captureHeaders.match(lower) {
			names = append(names, lower)
		}
	}
	if len(names) == 0 {
		return
	}
	sort.Strings(names)
	if len(names) > maxCapturedHeaders {
		names = names[:maxCapturedHeaders]
	}

	redactor := p.redactorFor(rc)
	mode := privacyMode(rc)
	for _, name := range names {
		v := strings.Join(rc.RequestHeaders.Values(name), headerValueSeparator)
		if v == "" {
			continue
		}
		// Redact before truncating: cutting first can sever the tail that made
		// a secret recognisable.
		span.SetAttribute(headerAttrPrefix+name, truncateAtRune(redact(v, redactor, mode), maxHeaderValueLen))
	}
}

// Not "extra_body": that is a Python-SDK keyword, not a wire concept.
const bodyAttrPrefix = "agentcc.body."

// Counts fields left out: non-scalars, credential-shaped names, and the caps.
const bodyDroppedAttr = "agentcc.body_dropped"

// attachBodyExtras emits the body's unknown top-level fields — extra_body, plus
// every OpenAI param newer than UnmarshalJSON's fixed list, which is why it
// defaults on. Already provenance-filtered upstream; this caps and redacts.
func (p *Plugin) attachBodyExtras(span *otelpkg.Span, rc *models.RequestContext) {
	if !p.bodyAttributes {
		return
	}
	dropped := rc.CallerExtrasDropped
	if len(rc.CallerExtras) == 0 {
		if dropped > 0 {
			span.SetAttribute(bodyDroppedAttr, dropped)
		}
		return
	}

	// Sorted, for the same reason as metadata.
	keys := make([]string, 0, len(rc.CallerExtras))
	for k := range rc.CallerExtras {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	redactor := p.redactorFor(rc)
	mode := privacyMode(rc)
	kept := 0
	for _, k := range keys {
		if len(k) > maxMetadataKeyLen || kept >= maxMetadataPairs {
			dropped++
			continue
		}
		v := rc.CallerExtras[k]
		if sv, ok := v.(string); ok {
			v = truncateAtRune(redact(sv, redactor, mode), maxMetadataValueLen)
		}
		span.SetAttribute(bodyAttrPrefix+k, v)
		kept++
	}
	if dropped > 0 {
		span.SetAttribute(bodyDroppedAttr, dropped)
	}
}
