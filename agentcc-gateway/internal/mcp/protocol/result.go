package protocol

import (
	"bytes"
	"encoding/json"
	"time"
)

// Result types of a 2026-07-28 result (resultType).
const (
	ResultComplete      = "complete"
	ResultInputRequired = "input_required"
)

// Cache scopes of a cacheable 2026-07-28 result. A private result may not be
// shared across authorization contexts (MCA C17).
const (
	CacheScopePrivate = "private"
	CacheScopePublic  = "public"
)

// cacheableMethods are the methods whose 2026 result carries ttlMs and
// cacheScope: lists, reads and discover.
var cacheableMethods = map[string]bool{
	"tools/list":               true,
	"resources/list":           true,
	"resources/templates/list": true,
	"prompts/list":             true,
	"resources/read":           true,
	"server/discover":          true,
}

// Cacheable reports whether a 2026 result of method carries caching fields.
func Cacheable(method string) bool { return cacheableMethods[method] }

// Envelope is what the gateway adds to a 2026-07-28 result as the server the
// client talks to.
type Envelope struct {
	// ServerInfo is the gateway's own serverInfo, written to the result's
	// _meta[MetaServerInfo] (the 2026-07-28 per-response field); it replaces
	// an upstream's.
	ServerInfo json.RawMessage
	// Cacheable adds ttlMs and cacheScope.
	Cacheable bool
	TTL       time.Duration
	// UpstreamTTL is set for a result passed through from an upstream (a
	// read): its ttlMs is the upstream's own, capped at TTL, or 0 when the
	// upstream named none, since the gateway cannot vouch for its freshness.
	UpstreamTTL bool
	// CacheScope is public or private; anything else is private.
	CacheScope string
}

// Decorate returns result, a JSON object, with the envelope's fields:
// resultType "complete" unless the result names its own (input_required from
// M28, task), _meta[MetaServerInfo], and on a cacheable result ttlMs and
// cacheScope. Every other member, the other _meta keys and unknown extension
// fields included, passes unchanged. A result that is not a JSON object is
// returned as it is.
func (e Envelope) Decorate(result json.RawMessage) json.RawMessage {
	var obj map[string]json.RawMessage
	if json.Unmarshal(result, &obj) != nil || obj == nil {
		return result
	}
	var rt string
	if json.Unmarshal(obj["resultType"], &rt) != nil || rt == "" {
		obj["resultType"] = json.RawMessage(`"` + ResultComplete + `"`)
	}
	if len(e.ServerInfo) > 0 {
		meta := map[string]json.RawMessage{}
		if json.Unmarshal(obj["_meta"], &meta) != nil || meta == nil {
			meta = map[string]json.RawMessage{}
		}
		meta[MetaServerInfo] = e.ServerInfo
		obj["_meta"], _ = json.Marshal(meta)
	}
	if e.Cacheable {
		obj["ttlMs"], _ = json.Marshal(e.ttlFor(obj["ttlMs"]))
		scope := e.CacheScope
		if scope != CacheScopePublic {
			scope = CacheScopePrivate
		}
		obj["cacheScope"], _ = json.Marshal(scope)
	}
	out, err := json.Marshal(obj)
	if err != nil {
		return result
	}
	return out
}

// ttlFor is the ttlMs of a result that carried upstream (maybe absent).
func (e Envelope) ttlFor(upstream json.RawMessage) int64 {
	ttl := e.TTL.Milliseconds()
	if !e.UpstreamTTL {
		return ttl
	}
	var up int64
	if json.Unmarshal(upstream, &up) != nil || up < 0 {
		return 0
	}
	return min(up, ttl)
}

// envelopeMembers are the 2026-07-28 caching members a legacy client never
// gets (with resultType "complete" and _meta[MetaServerInfo]).
var envelopeMembers = []string{"ttlMs", "cacheScope"}

// StripEnvelope returns result without the 2026-07-28 envelope: the
// top-level ttlMs and cacheScope, resultType when it is "complete" (the
// default, which tells a legacy client nothing) and _meta[MetaServerInfo]. A
// legacy client's answer that passes an upstream's result through needs it
// once the upstream speaks 2026-07-28 (M13's client detects the era), since a
// legacy result has none of them (W3b merge, M11 with M13). Another
// resultType (input_required, a parked call's) is left for its owner (M28) to
// translate. Every other member, nested ones and the other _meta keys
// included, passes unchanged; a result with none of them, or not a JSON
// object, is returned as it is.
func StripEnvelope(result json.RawMessage) json.RawMessage {
	if !bytes.Contains(result, []byte(`"resultType"`)) && !bytes.Contains(result, []byte(`"ttlMs"`)) &&
		!bytes.Contains(result, []byte(`"cacheScope"`)) && !bytes.Contains(result, []byte(MetaServerInfo)) {
		return result
	}
	var obj map[string]json.RawMessage
	if json.Unmarshal(result, &obj) != nil || obj == nil {
		return result
	}
	changed := false
	var rt string
	if raw, ok := obj["resultType"]; ok && json.Unmarshal(raw, &rt) == nil && rt == ResultComplete {
		delete(obj, "resultType")
		changed = true
	}
	for _, k := range envelopeMembers {
		if _, ok := obj[k]; ok {
			delete(obj, k)
			changed = true
		}
	}
	var meta map[string]json.RawMessage
	if raw, ok := obj["_meta"]; ok && json.Unmarshal(raw, &meta) == nil && meta != nil {
		if _, ok := meta[MetaServerInfo]; ok {
			delete(meta, MetaServerInfo)
			changed = true
			if len(meta) == 0 {
				delete(obj, "_meta")
			} else {
				obj["_meta"], _ = json.Marshal(meta)
			}
		}
	}
	if !changed {
		return result
	}
	out, err := json.Marshal(obj)
	if err != nil {
		return result
	}
	return out
}
