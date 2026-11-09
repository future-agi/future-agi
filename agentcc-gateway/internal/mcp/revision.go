package mcp

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sync"
)

// A tool's revision (M20) is the hex sha256 of its canonical definition, the
// form ToolsHash hashes a listing in: the tool projected onto name, title,
// description, inputSchema, outputSchema and annotations as the upstream sent
// them (canonicalTool). A change to any of those is a new revision; a change
// to anything else (icons, _meta) is not. A virtual server's overrides never
// change it: it is taken from the upstream's own definition.

// canonicalTool returns a tool definition's name and canonical form: the
// projection ToolsHash describes, as JSON with no insignificant whitespace,
// object keys sorted bytewise at every depth, number literals kept as
// written and HTML escaping off.
func canonicalTool(raw json.RawMessage) (string, []byte, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return "", nil, err
	}
	proj := make(map[string]any, len(probeToolFields))
	for _, k := range probeToolFields {
		if v, ok := fields[k]; ok {
			dec := json.NewDecoder(bytes.NewReader(v))
			dec.UseNumber()
			var val any
			if err := dec.Decode(&val); err != nil {
				return "", nil, fmt.Errorf("%s: %w", k, err)
			}
			proj[k] = val
		}
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(proj); err != nil {
		return "", nil, err
	}
	var name string
	_ = json.Unmarshal(fields["name"], &name)
	return name, bytes.TrimSuffix(buf.Bytes(), []byte("\n")), nil
}

// ToolRevision is the revision of one tool definition as an upstream sent it.
func ToolRevision(raw json.RawMessage) (string, error) {
	_, canon, err := canonicalTool(raw)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(canon)
	return hex.EncodeToString(sum[:]), nil
}

// revisionCacheMax bounds the revisions remembered by definition; past it the
// cache starts over, so a long-lived gateway never grows it without end.
const revisionCacheMax = 8192

// toolRevisions remembers each definition's revision and canonical form, by
// the definition's bytes and by revision, so a list or call does not hash an
// unchanged tool again, and a held tool's earlier definition stays known for
// its diff (M21) while this gateway has listed it.
var toolRevisions = &revisionCache{byRaw: map[string]revisionEntry{}, byRev: map[string][]byte{}}

type revisionEntry struct {
	rev   string
	canon []byte
}

type revisionCache struct {
	mu    sync.Mutex
	byRaw map[string]revisionEntry
	byRev map[string][]byte
}

// toolRevision returns t's revision and canonical definition, from the
// upstream's own bytes (Raw), or from t's fields when it has none. ok is false
// for a definition that has no canonical form.
func toolRevision(t Tool) (rev string, canon []byte, ok bool) {
	raw := t.Raw
	if len(raw) == 0 {
		b, err := json.Marshal(t)
		if err != nil {
			return "", nil, false
		}
		raw = b
	}
	c := toolRevisions
	c.mu.Lock()
	if e, hit := c.byRaw[string(raw)]; hit {
		c.mu.Unlock()
		return e.rev, e.canon, true
	}
	c.mu.Unlock()
	_, canon, err := canonicalTool(raw)
	if err != nil {
		return "", nil, false
	}
	sum := sha256.Sum256(canon)
	rev = hex.EncodeToString(sum[:])
	c.mu.Lock()
	if len(c.byRaw) >= revisionCacheMax {
		c.byRaw = map[string]revisionEntry{}
	}
	if len(c.byRev) >= revisionCacheMax {
		c.byRev = map[string][]byte{}
	}
	c.byRaw[string(raw)] = revisionEntry{rev: rev, canon: canon}
	c.byRev[rev] = canon
	c.mu.Unlock()
	return rev, canon, true
}

// knownDefinition returns the canonical definition of a revision this gateway
// has seen, if it still remembers it.
func knownDefinition(rev string) ([]byte, bool) {
	c := toolRevisions
	c.mu.Lock()
	defer c.mu.Unlock()
	canon, ok := c.byRev[rev]
	return canon, ok
}
