package vserver

import (
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Member is one entry of a virtual server's members list: a server id, a
// fagi.<managed_id>, or <server_id>@<account_label> (10X A8).
type Member struct {
	ID      string // as configured
	Server  string // the server the tools come from
	Account string // the account label, or ""
}

// ParseMember splits a members entry at its first "@".
func ParseMember(id string) Member {
	if server, account, ok := strings.Cut(id, "@"); ok && server != "" && account != "" {
		return Member{ID: id, Server: server, Account: account}
	}
	return Member{ID: id, Server: id}
}

// Key is the member's name prefix: the server id, or <server>_<account>.
func (m Member) Key() string {
	if m.Account == "" {
		return m.Server
	}
	return m.Server + "_" + m.Account
}

// LabelOK reports whether the member's account label, as exposed names
// carry it (normalized), keeps <server>_<label>__<name> readable back to
// its tool: no "__" in it and no "_" at either end, so the first "__" after
// the label always ends it and the name is never read as the server's own
// <server>__<name>. A label that fails exposes nothing (ReasonAccountLabel):
// otherwise an operator's blocked_tools could not tell the tool from its
// name (bug 22). A member with no account passes.
func (m Member) LabelOK() bool {
	if m.Account == "" {
		return true
	}
	l := sanitize(m.Account)
	return !strings.Contains(l, Separator) && !strings.HasPrefix(l, "_") && !strings.HasSuffix(l, "_")
}

// Tool is one upstream tool of a member, as the composer sees it.
type Tool struct {
	Member      Member
	Name        string // the upstream name
	Title       string
	Description string
}

// Ref is the tool's config key, <member>/<tool>: the key of overrides and
// the first name include, exclude and access globs match.
func (t Tool) Ref() string { return t.Member.ID + "/" + t.Name }

// Names are the forms a config glob matches: <member>/<tool>, and for an
// account member also <server>/<tool>, so "github/*" covers github@work.
func (t Tool) Names() []string {
	if t.Member.Account == "" {
		return []string{t.Ref()}
	}
	return []string{t.Ref(), t.Member.Server + "/" + t.Name}
}

// Spec is what a virtual server says about composition.
type Spec struct {
	Naming    string // NamingPrefixed (default) or NamingFlat
	Include   []string
	Exclude   []string
	Overrides map[string]*tenant.MCPToolOverride
	// Members are the configured members: an incumbent whose tool is absent
	// keeps its name only while its member is one of them.
	Members []string
}

// SpecOf reads a virtual server's composition settings.
func SpecOf(v tenant.MCPVirtualServerSettings) Spec {
	return Spec{Naming: v.Naming, Include: v.Include, Exclude: v.Exclude, Overrides: v.Overrides, Members: v.Members}
}

// Entry is one exposed tool. Title and Description are what clients see; the
// Original fields are the upstream's, so an override keeps applying, and the
// upstream values stay known, across an upstream refresh.
type Entry struct {
	Tool
	Exposed             string
	Title               string
	Description         string
	OriginalName        string
	OriginalDescription string
}

// Rejection reasons.
const (
	ReasonCollision = "collision"
	ReasonReserved  = "reserved"
	// ReasonAccountLabel: the tool's member has an account label its
	// exposed names cannot carry unambiguously (Member.LabelOK).
	ReasonAccountLabel = "account_label"
)

// Rejection is a tool left out because of its exposed name, never silently
// overwritten: mcp_tool_name_collision.
type Rejection struct {
	Ref          string // <member>/<tool>
	Exposed      string
	Reason       string // ReasonCollision, ReasonReserved or ReasonAccountLabel
	CollidesWith string // the holder's ref, for a collision
	// Quarantined is set when the tool appeared through a refresh and the
	// name was already exposed by the incumbent, which keeps it (MC.3
	// tool_quarantined, reason: collision; M20 shows it, M21 resolves it).
	Quarantined bool
	// Tool is the rejected tool, so a call by its name can be judged.
	Tool Tool
}

// View is a composed virtual server.
type View struct {
	// Entries are sorted by exposed name, byte order.
	Entries  []Entry
	Rejected []Rejection
	index    map[string]int
}

// Lookup finds an exposed tool by its exact exposed name.
func (v *View) Lookup(exposed string) (Entry, bool) {
	if v == nil {
		return Entry{}, false
	}
	i, ok := v.index[exposed]
	if !ok {
		return Entry{}, false
	}
	return v.Entries[i], true
}

// Quarantined finds a quarantined newcomer by its exact exposed name: a
// name no entry of the view holds exactly but that a refresh's newcomer
// would have taken (a case-folded collision, or an incumbent away).
func (v *View) Quarantined(exposed string) (Entry, bool) {
	if v == nil {
		return Entry{}, false
	}
	for _, r := range v.Rejected {
		if r.Quarantined && r.Exposed == exposed {
			return Entry{Tool: r.Tool, Exposed: r.Exposed}, true
		}
	}
	return Entry{}, false
}

// Holders maps a case-folded exposed name to the ref of the tool that holds
// it. Compose returns the holders of its view; passing them to the next
// composition of the same virtual server keeps every incumbent's name.
type Holders map[string]string

type candidate struct {
	entry     Entry
	fold      string
	incumbent bool
}

// Compose builds a view: tools filtered by include and exclude, hidden
// overrides removed, named, the tools of a member whose account label fails
// LabelOK, collisions and reserved names rejected (the incumbent of prev
// keeps its name, else the lowest ref), and sorted. An incumbent whose tool
// is absent from tools still holds its name against every other member's
// tool (absentIncumbents).
func Compose(spec Spec, tools []Tool, prev Holders) (*View, Holders) {
	cands := make([]candidate, 0, len(tools))
	present := make(map[string]bool, len(tools))
	for _, t := range tools {
		present[t.Ref()] = true
		ov, ok := spec.exposes(t)
		if !ok {
			continue
		}
		e := Entry{Tool: t, Title: t.Title, Description: t.Description, OriginalName: t.Name, OriginalDescription: t.Description}
		e.Exposed = spec.exposedName(t, ov)
		if ov != nil {
			if ov.Title != "" {
				e.Title = ov.Title
			}
			if ov.Description != "" {
				e.Description = ov.Description
			}
		}
		fold := FoldKey(e.Exposed)
		cands = append(cands, candidate{entry: e, fold: fold, incumbent: prev[fold] == t.Ref()})
	}
	sort.SliceStable(cands, func(i, j int) bool {
		if cands[i].incumbent != cands[j].incumbent {
			return cands[i].incumbent
		}
		return cands[i].entry.Ref() < cands[j].entry.Ref()
	})

	view := &View{index: map[string]int{}}
	holders := Holders{}
	absent := spec.absentIncumbents(prev, present)
	for _, c := range cands {
		ref := c.entry.Ref()
		holder, taken := holders[c.fold]
		away, haunted := absent[c.fold]
		switch {
		case !c.entry.Member.LabelOK():
			view.Rejected = append(view.Rejected, Rejection{Ref: ref, Exposed: c.entry.Exposed, Reason: ReasonAccountLabel, Tool: c.entry.Tool})
		case IsReserved(c.entry.Exposed):
			view.Rejected = append(view.Rejected, Rejection{Ref: ref, Exposed: c.entry.Exposed, Reason: ReasonReserved, Tool: c.entry.Tool})
		case taken:
			_, held := prev[c.fold]
			view.Rejected = append(view.Rejected, Rejection{Ref: ref, Exposed: c.entry.Exposed, Reason: ReasonCollision,
				CollidesWith: holder, Quarantined: held && prev[c.fold] == holder, Tool: c.entry.Tool})
		case haunted && spec.memberOf(away) != c.entry.Member.ID:
			view.Rejected = append(view.Rejected, Rejection{Ref: ref, Exposed: c.entry.Exposed, Reason: ReasonCollision,
				CollidesWith: away, Quarantined: true, Tool: c.entry.Tool})
		default:
			holders[c.fold] = ref
			view.Entries = append(view.Entries, c.entry)
		}
	}
	for fold, ref := range absent {
		if _, taken := holders[fold]; !taken {
			holders[fold] = ref
		}
	}
	sort.Slice(view.Entries, func(i, j int) bool { return view.Entries[i].Exposed < view.Entries[j].Exposed })
	for i, e := range view.Entries {
		view.index[e.Exposed] = i
	}
	return view, holders
}

// exposes reports whether the virtual server exposes t (include, exclude and
// a hidden override), with t's override.
func (s Spec) exposes(t Tool) (*tenant.MCPToolOverride, bool) {
	names := t.Names()
	if len(s.Include) > 0 && !Any(s.Include, names...) || Any(s.Exclude, names...) {
		return nil, false
	}
	ov := s.override(t)
	if ov != nil && ov.Hidden != nil && *ov.Hidden {
		return nil, false
	}
	return ov, true
}

// absentIncumbents are the holders in prev whose tool is absent from this
// composition although its member is still configured and the config would
// still expose it under the same name. Each keeps its name against another
// member's tool, so an upstream outage, a reconnect or a refresh that drops
// the tool never hands the name to a newcomer (tool shadowing); the newcomer
// is quarantined until the incumbent returns or an admin resolves it with an
// exclude, an override or by removing the member.
func (s Spec) absentIncumbents(prev Holders, present map[string]bool) Holders {
	out := Holders{}
	for fold, ref := range prev {
		member := s.memberOf(ref)
		if present[ref] || member == "" {
			continue
		}
		t := Tool{Member: ParseMember(member), Name: strings.TrimPrefix(ref, member+"/")}
		if ov, ok := s.exposes(t); ok && FoldKey(s.exposedName(t, ov)) == fold {
			out[fold] = ref
		}
	}
	return out
}

// memberOf is the configured member a tool ref belongs to, "" for none: the
// longest member id that prefixes ref with "/", so a "/" in a tool name is
// never read as part of the member.
func (s Spec) memberOf(ref string) string {
	best := ""
	for _, m := range s.Members {
		if len(m) > len(best) && strings.HasPrefix(ref, m+"/") {
			best = m
		}
	}
	return best
}

// override is the tool's override: keyed by its member ref, else, for an
// account member, by its server ref.
func (s Spec) override(t Tool) *tenant.MCPToolOverride {
	for _, name := range t.Names() {
		if ov, ok := s.Overrides[name]; ok && ov != nil {
			return ov
		}
	}
	return nil
}

func (s Spec) exposedName(t Tool, ov *tenant.MCPToolOverride) string {
	if ov != nil && ov.Name != "" {
		return Normalize(ov.Name)
	}
	if s.Naming == NamingFlat {
		return Normalize(t.Name)
	}
	return EncodePrefixed(t.Member.Key(), t.Name)
}

// Any reports whether any pattern matches any of names. A pattern's "*"
// matches any run of characters, "/" included, and every other character
// matches itself: the glob the catalog and the org's managed lists use. It is
// not imported from internal/catalog, whose package init loads the model
// database, which an MCP process (a stdio helper included) must not pay for.
func Any(patterns []string, names ...string) bool {
	for _, p := range patterns {
		for _, n := range names {
			if globMatch(p, n) {
				return true
			}
		}
	}
	return false
}

func globMatch(pattern, s string) bool {
	parts := strings.Split(pattern, "*")
	if len(parts) == 1 {
		return pattern == s
	}
	if !strings.HasPrefix(s, parts[0]) {
		return false
	}
	s = s[len(parts[0]):]
	for _, p := range parts[1 : len(parts)-1] {
		i := strings.Index(s, p)
		if i < 0 {
			return false
		}
		s = s[i+len(p):]
	}
	return strings.HasSuffix(s, parts[len(parts)-1])
}
