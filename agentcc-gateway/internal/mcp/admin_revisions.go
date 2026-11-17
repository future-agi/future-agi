package mcp

import (
	"bytes"
	"encoding/json"
	"sort"
	"sync"
	"time"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/mcp/pin"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The tool-revision admin API: which tools of an org's own servers
// pinning holds or warns about, and what changed. It is computed from the
// org's pool and its pinned revisions on every request, so every replica
// answers the same and nothing is stored but the definitions this gateway
// has listed (for the "before" side of a diff) and when it first saw each
// held revision.

// pinsOn reports whether the wiring installed org pinning (OrgPins); with the
// default Deps.Pins nothing is pinned, so nothing is held or reported.
func (s *Server) pinsOn() bool {
	_, ok := s.Deps().Pins.(OrgPins)
	return ok
}

// pinPolicy is the pinning of an org's snapshot.
func pinPolicy(snapshot *tenant.OrgConfig) pin.Policy {
	return pin.NewPolicy(snapshot.MCPPinning())
}

// pinnedServer reports whether a pool server's tools are pinned: one the org
// saved itself, as checkPin decides for a call.
func pinnedServer(ps PoolServer) bool {
	return ps.Source == poolSourceOrg && !ps.Builtin && !ps.Managed
}

// adminPinState is a listed tool's revision and whether pinning holds it, for
// GET /-/mcp/tools (MCPAdminTool revision and quarantined).
func (s *Server) adminPinState(policy pin.Policy, ps PoolServer, t Tool) (rev *string, held bool) {
	r, _, ok := toolRevision(t)
	if !ok {
		return nil, false
	}
	if s.pinsOn() && pinnedServer(ps) {
		held = policy.Judge(ps.ID, t.Name, r).Held
	}
	return &r, held
}

// AdminRevisions is GET /-/mcp/revisions?org_id= (mcp-revision-v1 each): every
// tool of the org's own servers whose revision is not the one an admin
// accepted (changed) or that has no pin on a server with pins (new), with
// its canonical definition and, when this gateway has listed the accepted
// revision, that definition and a field-level diff. A new tool's diff lists
// the fields it arrives with. Sorted by server, then tool.
func (s *Server) AdminRevisions(orgID string) ([]*gatewayadmin.MCPRevision, error) {
	v, err := s.orgPool(orgID)
	if err != nil {
		return nil, err
	}
	out := []*gatewayadmin.MCPRevision{}
	if !s.pinsOn() {
		return out, nil
	}
	policy := pinPolicy(v.snapshot)
	now := s.Deps().Clock()
	for _, ps := range v.pool.Servers() {
		if !pinnedServer(ps) {
			continue
		}
		seen := map[string]bool{}
		for _, t := range ps.Tools {
			if seen[t.Name] {
				continue
			}
			seen[t.Name] = true
			rev, canon, ok := toolRevision(t)
			if !ok {
				continue
			}
			verdict := policy.Judge(ps.ID, t.Name, rev)
			if verdict.Reason == "" {
				continue
			}
			out = append(out, revisionRecord(orgID, ps.ID, t.Name, rev, canon, verdict, firstSeen(orgID, ps.ID, t.Name, rev, now)))
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].ServerID != out[j].ServerID {
			return out[i].ServerID < out[j].ServerID
		}
		return out[i].Tool < out[j].Tool
	})
	return out, nil
}

// revisionRecord is one mcp-revision-v1 record.
func revisionRecord(orgID, serverID, tool, rev string, canon []byte, v pin.Verdict, seenAt time.Time) *gatewayadmin.MCPRevision {
	r := &gatewayadmin.MCPRevision{
		OrgID: orgID, ServerID: serverID, Tool: tool, Revision: rev, Reason: v.Reason,
		Definition:  jsonObject(canon),
		FirstSeenAt: seenAt.UTC().Format(time.RFC3339),
		Diff:        []*gatewayadmin.MCPRevisionDiff{},
	}
	var before []byte
	switch {
	case v.Expected != "":
		expected := v.Expected
		r.PreviousRevision = &expected
		prev, known := knownDefinition(expected)
		if !known {
			return r // the accepted definition was never listed here: no diff
		}
		r.PreviousDefinition = jsonObject(prev)
		before = prev
	case v.Reason != pin.ReasonNew:
		return r
	}
	for _, c := range pin.Diff(before, canon) {
		r.Diff = append(r.Diff, &gatewayadmin.MCPRevisionDiff{Field: c.Field, Before: jsonValue(c.Before), After: jsonValue(c.After)})
	}
	return r
}

// jsonValue decodes a canonical JSON value keeping number literals as written
// (json.Number); nil for an absent one.
func jsonValue(raw json.RawMessage) any {
	if len(raw) == 0 {
		return nil
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if dec.Decode(&v) != nil {
		return nil
	}
	return v
}

// jsonObject decodes a canonical definition as an object, numbers as written.
func jsonObject(raw []byte) map[string]any {
	obj, _ := jsonValue(raw).(map[string]any)
	if obj == nil {
		obj = map[string]any{}
	}
	return obj
}

// firstSeenMax bounds the remembered first sightings; past it the map starts
// over (a held revision then reads as first seen at that moment).
const firstSeenMax = 8192

var firstSeenAt = struct {
	sync.Mutex
	m map[string]time.Time
}{m: map[string]time.Time{}}

// firstSeen is when this gateway first reported a held revision.
func firstSeen(orgID, serverID, tool, rev string, now time.Time) time.Time {
	key := orgID + "\x00" + serverID + "\x00" + tool + "\x00" + rev
	firstSeenAt.Lock()
	defer firstSeenAt.Unlock()
	if at, ok := firstSeenAt.m[key]; ok {
		return at
	}
	if len(firstSeenAt.m) >= firstSeenMax {
		firstSeenAt.m = map[string]time.Time{}
	}
	firstSeenAt.m[key] = now
	return now
}
