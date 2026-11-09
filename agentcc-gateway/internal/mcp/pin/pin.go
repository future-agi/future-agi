// Package pin is tool-definition pinning (M20, decision 33): which tools of an
// org's own MCP servers are held because their definition is not the one an
// admin accepted, and what changed. It is pure: the gateway's hooks feed it
// each tool's revision, and the org config's mcp.pinning holds the pins.
package pin

import (
	"bytes"
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Modes and new-tool settings of mcp.pinning.
const (
	ModeOff        = "off"
	ModeWarn       = "warn"
	ModeQuarantine = "quarantine"
	NewToolsAllow  = "allow"
)

// Reasons a tool is not served as pinned (mcp-revision-v1 reason).
const (
	ReasonChanged = "changed"
	ReasonNew     = "new"
)

// Policy is an org's pinning, ready to judge tools.
type Policy struct {
	mode         string
	allowNew     bool
	autoBaseline bool
	pins         map[string]string
	pinned       map[string]bool // servers with at least one pin
}

// NewPolicy reads an org's mcp.pinning (defaults applied). A mode it does
// not know is quarantine, so a bad value never serves a changed tool.
func NewPolicy(s tenant.MCPPinningSettings) Policy {
	p := Policy{mode: s.Mode, allowNew: s.NewTools == NewToolsAllow, autoBaseline: s.AutoBaseline, pins: s.Pins}
	if p.mode != ModeOff && p.mode != ModeWarn {
		p.mode = ModeQuarantine
	}
	p.pinned = map[string]bool{}
	for key := range s.Pins {
		if server, _, ok := strings.Cut(key, "/"); ok {
			p.pinned[server] = true
		}
	}
	return p
}

// Key is the pins key of a server's tool: <server_id>/<tool>.
func Key(serverID, tool string) string { return serverID + "/" + tool }

// Verdict is how pinning treats one tool at one revision.
type Verdict struct {
	// Reason is ReasonChanged or ReasonNew when the revision is not one an
	// admin accepted, "" when it is (or pinning does not apply).
	Reason string
	// Expected is the accepted revision of a changed tool.
	Expected string
	// Held: the tool is not exposed and a call to it is refused until an
	// admin accepts the revision (mode quarantine). In mode warn a tool with
	// a Reason is still served, with a warning.
	Held bool
}

// Judge returns the verdict for the tool of serverID at revision rev:
//   - pinned at rev: served;
//   - pinned at another revision: changed, held in quarantine mode;
//   - not pinned, on a server with no pins at all and auto_baseline on:
//     served (the server's first listing is its baseline, which Django pins);
//   - not pinned otherwise: new, held in quarantine mode unless new_tools is
//     allow.
//
// Mode off serves everything.
func (p Policy) Judge(serverID, tool, rev string) Verdict {
	if p.mode == ModeOff {
		return Verdict{}
	}
	if want, ok := p.pins[Key(serverID, tool)]; ok {
		if want == rev {
			return Verdict{}
		}
		return Verdict{Reason: ReasonChanged, Expected: want, Held: p.mode == ModeQuarantine}
	}
	if !p.pinned[serverID] && p.autoBaseline {
		return Verdict{}
	}
	return Verdict{Reason: ReasonNew, Held: p.mode == ModeQuarantine && !p.allowNew}
}

// Baselining reports whether serverID's tools are served as its baseline:
// pinning is on, auto_baseline is on and the server has no pin yet.
func (p Policy) Baselining(serverID string) bool {
	return p.mode != ModeOff && p.autoBaseline && !p.pinned[serverID]
}

// Fields are the definition fields a revision covers, in diff order.
var Fields = []string{"name", "title", "description", "inputSchema", "outputSchema", "annotations"}

// Change is one field of a definition that differs (mcp-revision-v1 diff):
// Before or After is nil when the field is absent on that side.
type Change struct {
	Field  string          `json:"field"`
	Before json.RawMessage `json:"before,omitempty"`
	After  json.RawMessage `json:"after,omitempty"`
}

// Diff lists the fields whose canonical values differ between two canonical
// definitions, in Fields order. A definition that is not a JSON object reads
// as empty.
func Diff(before, after []byte) []Change {
	b, a := fieldsOf(before), fieldsOf(after)
	out := []Change{}
	for _, f := range Fields {
		if !bytes.Equal(b[f], a[f]) {
			out = append(out, Change{Field: f, Before: b[f], After: a[f]})
		}
	}
	return out
}

func fieldsOf(canon []byte) map[string]json.RawMessage {
	var m map[string]json.RawMessage
	if len(canon) == 0 || json.Unmarshal(canon, &m) != nil {
		return map[string]json.RawMessage{}
	}
	return m
}
