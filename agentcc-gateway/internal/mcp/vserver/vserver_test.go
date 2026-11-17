package vserver

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func tool(member, name string) Tool {
	return Tool{Member: ParseMember(member), Name: name, Description: "upstream " + name}
}

func exposed(v *View) []string {
	out := make([]string, 0, len(v.Entries))
	for _, e := range v.Entries {
		out = append(out, e.Exposed)
	}
	return out
}

var nameCharset = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)

func TestNameEncodingCap64AndHash(t *testing.T) {
	if got := EncodePrefixed("github", "create_issue"); got != "github__create_issue" {
		t.Fatalf("prefixed = %q", got)
	}
	if got := EncodePrefixed("git hub", "a/b:c"); got != "git_hub__a_b_c" {
		t.Fatalf("disallowed characters = %q, want each mapped to _", got)
	}
	long := EncodePrefixed("server", strings.Repeat("t", 80))
	full := "server__" + strings.Repeat("t", 80)
	sum := sha256.Sum256([]byte(full))
	want := full[:55] + "_" + hex.EncodeToString(sum[:])[:8]
	if long != want || len(long) != MaxNameLen {
		t.Fatalf("long name = %q (%d), want %q (64)", long, len(long), want)
	}
	if !nameCharset.MatchString(long) {
		t.Fatalf("capped name %q leaves the charset", long)
	}
	// Two long names sharing their first 55 characters still differ.
	other := EncodePrefixed("server", strings.Repeat("t", 79)+"u")
	if other == long || len(other) != MaxNameLen {
		t.Fatalf("two long names encode alike: %q", other)
	}
	// Exactly 64 is kept as it is.
	exact := strings.Repeat("a", 64)
	if Normalize(exact) != exact {
		t.Fatal("a 64-character name was hashed")
	}
	// Flat naming keeps the tool's own name, normalized the same way.
	v, _ := Compose(Spec{Naming: NamingFlat}, []Tool{tool("github", "create_issue")}, nil)
	if got := exposed(v); len(got) != 1 || got[0] != "create_issue" {
		t.Fatalf("flat = %v", got)
	}
}

func TestCollisionRejectedNotOverwritten(t *testing.T) {
	// Under prefixed naming the bug-13 pair stays apart.
	v, _ := Compose(Spec{}, []Tool{tool("git_hub", "search"), tool("git", "hub_search")}, nil)
	if got := strings.Join(exposed(v), ","); got != "git__hub_search,git_hub__search" {
		t.Fatalf("prefixed view = %s", got)
	}
	// Under flat naming two servers' "search" collide, case-folded too: the
	// lowest ref keeps the name and the other is rejected, never overwritten.
	v, _ = Compose(Spec{Naming: NamingFlat}, []Tool{tool("zeta", "Search"), tool("alpha", "search"), tool("mid", "other")}, nil)
	if got := strings.Join(exposed(v), ","); got != "other,search" {
		t.Fatalf("flat view = %s, want other,search", got)
	}
	if e, _ := v.Lookup("search"); e.Ref() != "alpha/search" {
		t.Fatalf("search is held by %s, want alpha/search", e.Ref())
	}
	if len(v.Rejected) != 1 || v.Rejected[0].Ref != "zeta/Search" || v.Rejected[0].Reason != ReasonCollision ||
		v.Rejected[0].CollidesWith != "alpha/search" || v.Rejected[0].Quarantined {
		t.Fatalf("rejected = %+v", v.Rejected)
	}
	// Reserved meta-tool names are rejected in any case.
	v, _ = Compose(Spec{Naming: NamingFlat}, []Tool{tool("a", "Search_Tools"), tool("a", "ok")}, nil)
	if got := strings.Join(exposed(v), ","); got != "ok" || v.Rejected[0].Reason != ReasonReserved {
		t.Fatalf("reserved: view %s, rejected %+v", got, v.Rejected)
	}
	// An override name collides like any other.
	over := map[string]*tenant.MCPToolOverride{"a/x": {Name: "b__y"}}
	v, _ = Compose(Spec{Overrides: over}, []Tool{tool("a", "x"), tool("b", "y")}, nil)
	if len(v.Entries) != 1 || len(v.Rejected) != 1 {
		t.Fatalf("override collision: entries %v, rejected %+v", exposed(v), v.Rejected)
	}
}

func TestRefreshCollisionQuarantinesNewcomer(t *testing.T) {
	spec := Spec{Naming: NamingFlat}
	v, holders := Compose(spec, []Tool{tool("zeta", "search")}, nil)
	if got := exposed(v); len(got) != 1 {
		t.Fatalf("first view = %v", got)
	}
	// A refresh brings alpha/search, which sorts first: the incumbent keeps
	// the name and the newcomer is quarantined.
	v, holders = Compose(spec, []Tool{tool("zeta", "search"), tool("alpha", "search")}, holders)
	if e, _ := v.Lookup("search"); e.Ref() != "zeta/search" {
		t.Fatalf("after the refresh search is held by %s, want the incumbent zeta/search", e.Ref())
	}
	if len(v.Rejected) != 1 || !v.Rejected[0].Quarantined || v.Rejected[0].Ref != "alpha/search" || v.Rejected[0].CollidesWith != "zeta/search" {
		t.Fatalf("rejected = %+v, want alpha/search quarantined", v.Rejected)
	}
	// It stays the incumbent on every later composition.
	v, _ = Compose(spec, []Tool{tool("alpha", "search"), tool("zeta", "search")}, holders)
	if e, _ := v.Lookup("search"); e.Ref() != "zeta/search" || !v.Rejected[0].Quarantined {
		t.Fatalf("later composition: holder %s, rejected %+v", e.Ref(), v.Rejected)
	}
}

func TestOverridesSurviveRefresh(t *testing.T) {
	hidden := true
	spec := Spec{Overrides: map[string]*tenant.MCPToolOverride{
		"github/create_issue": {Name: "new_issue", Title: "New issue", Description: "Open an issue"},
		"github/delete_repo":  {Hidden: &hidden},
	}}
	tools := []Tool{tool("github", "create_issue"), tool("github", "delete_repo"), tool("github", "list")}
	v, holders := Compose(spec, tools, nil)
	e, ok := v.Lookup("new_issue")
	if !ok || e.Title != "New issue" || e.Description != "Open an issue" || e.OriginalName != "create_issue" || e.OriginalDescription != "upstream create_issue" {
		t.Fatalf("overridden entry = %+v", e)
	}
	if _, ok := v.Lookup("github__delete_repo"); ok {
		t.Fatal("a hidden tool is exposed")
	}
	// The upstream changes the description: the override still applies and
	// the original fields follow the upstream.
	tools[0].Description = "upstream v2"
	v, _ = Compose(spec, tools, holders)
	e, ok = v.Lookup("new_issue")
	if !ok || e.Description != "Open an issue" || e.OriginalDescription != "upstream v2" || e.OriginalName != "create_issue" {
		t.Fatalf("after the refresh = %+v", e)
	}
	if got := strings.Join(exposed(v), ","); got != "github__list,new_issue" {
		t.Fatalf("view = %s", got)
	}
}

func TestListsSortedStable(t *testing.T) {
	var tools []Tool
	for _, m := range []string{"zeta", "alpha", "mid"} {
		for _, n := range []string{"q", "b", "x", "a"} {
			tools = append(tools, tool(m, n))
		}
	}
	v, _ := Compose(Spec{}, tools, nil)
	first := strings.Join(exposed(v), ",")
	for i := 0; i < 20; i++ {
		// Reverse the input each time: the order must not depend on it.
		for l, r := 0, len(tools)-1; l < r; l, r = l+1, r-1 {
			tools[l], tools[r] = tools[r], tools[l]
		}
		v, _ = Compose(Spec{}, tools, nil)
		if got := strings.Join(exposed(v), ","); got != first {
			t.Fatalf("composition %d = %s, want %s", i, got, first)
		}
	}
	names := exposed(v)
	for i := 1; i < len(names); i++ {
		if names[i-1] >= names[i] {
			t.Fatalf("not sorted by byte order: %q before %q", names[i-1], names[i])
		}
	}
}

func TestAccountQualifiedMemberNames(t *testing.T) {
	m := ParseMember("github@work")
	if m.Server != "github" || m.Account != "work" || m.Key() != "github_work" {
		t.Fatalf("member = %+v", m)
	}
	v, _ := Compose(Spec{Include: []string{"github/*"}}, []Tool{tool("github@work", "create_issue"), tool("gitlab", "x")}, nil)
	if got := exposed(v); len(got) != 1 || got[0] != "github_work__create_issue" {
		t.Fatalf("view = %v, want github_work__create_issue (a server glob covers its accounts)", got)
	}
	long := EncodePrefixed(m.Key(), strings.Repeat("z", 70))
	if len(long) != MaxNameLen {
		t.Fatalf("account name not capped: %d", len(long))
	}
	// A server named github_work beside the member github@work collides.
	v, _ = Compose(Spec{}, []Tool{tool("github@work", "t"), tool("github_work", "t")}, nil)
	if len(v.Entries) != 1 || len(v.Rejected) != 1 || v.Rejected[0].Reason != ReasonCollision {
		t.Fatalf("account collision: %v, %+v", exposed(v), v.Rejected)
	}

	// A label is refused when, normalized, it holds "__" or starts or ends
	// with "_": <server>_<label>__<tool> could then not be read back to its
	// tool (the first "__" would not end the label, or the name would read as
	// the server's own <server>__<tool>). Every tool of such a member is
	// rejected, under any naming and even when renamed; it never takes a name.
	for label, ok := range map[string]bool{"work": true, "a_b": true, "a.b-c": true, "a b": true, "x": true,
		"my__acct": false, "_x": false, "x_": false, "a  b": false, "work!": false, "!work": false, "a__b__c": false} {
		if got := ParseMember("github@" + label).LabelOK(); got != ok {
			t.Errorf("LabelOK(%q) = %v, want %v", label, got, ok)
		}
	}
	if !ParseMember("github").LabelOK() {
		t.Fatal("a member with no account was refused")
	}
	over := map[string]*tenant.MCPToolOverride{"github@x_/t": {Name: "renamed"}}
	for _, spec := range []Spec{{}, {Naming: NamingFlat}, {Overrides: over}} {
		v, _ = Compose(spec, []Tool{tool("github@x_", "t"), tool("github@my__acct", "u"), tool("github@a_b", "w")}, nil)
		if got := strings.Join(exposed(v), ","); got != map[string]string{"": "github_a_b__w", NamingFlat: "w"}[spec.Naming] {
			t.Fatalf("naming %q: view %s, want only the a_b account's tool", spec.Naming, got)
		}
		if len(v.Rejected) != 2 || v.Rejected[0].Reason != ReasonAccountLabel || v.Rejected[1].Reason != ReasonAccountLabel {
			t.Fatalf("naming %q: rejected %+v, want both refused labels' tools as account_label", spec.Naming, v.Rejected)
		}
	}
	// A refused label's tool does not hold its name against another member.
	v, _ = Compose(Spec{Naming: NamingFlat}, []Tool{tool("aaa@_x", "t"), tool("zzz", "t")}, nil)
	if e, ok := v.Lookup("t"); !ok || e.Ref() != "zzz/t" {
		t.Fatalf("flat t is held by %+v, want zzz/t", e)
	}
}

func TestAccessRulesKeyAgentUser(t *testing.T) {
	rules := []*tenant.MCPAccessRule{
		{Match: &tenant.MCPAccessRuleMatch{KeyIDs: []string{"k1"}}, Allow: []string{"github/list*"}},
		{Match: &tenant.MCPAccessRuleMatch{AgentIDs: []string{"a1"}}, Deny: []string{"github/delete_*"}},
		{Match: &tenant.MCPAccessRuleMatch{KeyIDs: []string{"k2"}, UserIDs: []string{"u1"}}, Deny: []string{"*"}},
	}
	for _, tc := range []struct {
		p    Principal
		name string
		want string
	}{
		{Principal{KeyID: "k1"}, "github/list_repos", ""},
		{Principal{KeyID: "k1"}, "github/create_issue", "access.rules[0]"},
		{Principal{KeyID: "k9", AgentID: "a1"}, "github/delete_repo", "access.rules[1]"},
		{Principal{KeyID: "k9", AgentID: "a1"}, "github/create_issue", ""},
		{Principal{KeyID: "k1", AgentID: "a1"}, "github/list_delete", ""},
		{Principal{KeyID: "k2"}, "github/x", ""}, // rule 2 needs both k2 and u1
		{Principal{KeyID: "k2", UserID: "u1"}, "github/x", "access.rules[2]"},
		{Principal{}, "github/delete_repo", ""}, // an empty id matches no list
	} {
		if got := DeniedByRules(rules, tc.p, []string{tc.name}, []string{tc.name}); got != tc.want {
			t.Errorf("DeniedByRules(%+v, %s) = %q, want %q", tc.p, tc.name, got, tc.want)
		}
	}
	// A rule with no criteria matches everyone; deny wins over another rule's allow.
	all := []*tenant.MCPAccessRule{{Allow: []string{"*"}}, {Match: &tenant.MCPAccessRuleMatch{}, Deny: []string{"a/x"}}}
	if got := DeniedByRules(all, Principal{KeyID: "k"}, []string{"a/x"}, []string{"a/x"}); got != "access.rules[1]" {
		t.Fatalf("deny did not win: %q", got)
	}
	// The allow side sees only the names it is given; the deny side its own.
	allowOnly := []*tenant.MCPAccessRule{{Allow: []string{"renamed"}}}
	if got := DeniedByRules(allowOnly, Principal{}, []string{"a/x"}, []string{"a/x", "renamed"}); got != "access.rules[0]" {
		t.Fatalf("an allow matched a deny-side name: %q", got)
	}
	denyOnly := []*tenant.MCPAccessRule{{Deny: []string{"renamed"}}}
	if got := DeniedByRules(denyOnly, Principal{}, []string{"a/x"}, []string{"a/x", "renamed"}); got != "access.rules[0]" {
		t.Fatalf("a deny missed a deny-side name: %q", got)
	}

	yes := true
	for _, tc := range []struct {
		a    tenant.MCPVirtualServerAccess
		p    Principal
		want bool
	}{
		{tenant.MCPVirtualServerAccess{}, Principal{KeyID: "k"}, true},
		{tenant.MCPVirtualServerAccess{Keys: []string{"k1"}}, Principal{KeyID: "k2"}, false},
		{tenant.MCPVirtualServerAccess{Keys: []string{"k1"}}, Principal{KeyID: "k1"}, true},
		{tenant.MCPVirtualServerAccess{Agents: []string{"a"}}, Principal{KeyID: "k", AgentID: "a"}, true},
		{tenant.MCPVirtualServerAccess{Users: []string{"u"}}, Principal{KeyID: "k", UserID: "u"}, true},
		{tenant.MCPVirtualServerAccess{Keys: []string{"k1"}, DefaultForOrg: &yes}, Principal{KeyID: "k2"}, true},
	} {
		if got := Usable(tc.a, tc.p); got != tc.want {
			t.Errorf("Usable(%+v, %+v) = %v, want %v", tc.a, tc.p, got, tc.want)
		}
	}
}

func TestHeaderNarrowsOnly(t *testing.T) {
	if Outside(nil, "a/x") {
		t.Fatal("an unset list narrowed")
	}
	if !Outside([]string{"b/*"}, "a/x", "a__x") || Outside([]string{"a__*"}, "a/x", "a__x") {
		t.Fatal("Outside does not match the exposed name or the ref")
	}
}

func TestGlobMatch(t *testing.T) {
	for _, tc := range []struct {
		pattern, s string
		want       bool
	}{
		{"github/*", "github/create_issue", true},
		{"github/*", "gitlab/x", false},
		{"*", "a/b", true},
		{"*/delete_*", "github/delete_repo", true},
		{"*/delete_*", "github/undelete", false},
		{"a*b*c", "aXbYc", true},
		{"a*b*c", "aXcYb", false},
		{"exact", "exact", true},
		{"exact", "exactly", false},
		{"ab*ba", "aba", false}, // the prefix and suffix may not overlap
	} {
		if got := globMatch(tc.pattern, tc.s); got != tc.want {
			t.Errorf("globMatch(%q, %q) = %v, want %v", tc.pattern, tc.s, got, tc.want)
		}
	}
}

// TestAbsentIncumbentKeepsItsName is the tool-shadowing case of a refresh:
// while the incumbent's upstream lists nothing (an outage, a reconnect) or
// drops the tool, another member's tool of the same name never takes it; it
// is quarantined behind the incumbent, which gets the name back when it
// returns. The incumbent's own member may rename it, and an exclude, a
// renaming override or removing the member (an admin's resolution) releases
// the name.
func TestAbsentIncumbentKeepsItsName(t *testing.T) {
	spec := Spec{Naming: NamingFlat, Members: []string{"trusted", "other"}}
	_, holders := Compose(spec, []Tool{tool("trusted", "deploy")}, nil)

	v, held := Compose(spec, []Tool{tool("other", "deploy")}, holders)
	if len(v.Entries) != 0 || len(v.Rejected) != 1 || v.Rejected[0].Ref != "other/deploy" || !v.Rejected[0].Quarantined ||
		v.Rejected[0].CollidesWith != "trusted/deploy" || v.Rejected[0].Reason != ReasonCollision {
		t.Fatalf("with the incumbent absent: entries %v, rejected %+v; want other/deploy quarantined", exposed(v), v.Rejected)
	}
	v, _ = Compose(spec, []Tool{tool("other", "deploy"), tool("trusted", "deploy")}, held)
	if e, _ := v.Lookup("deploy"); e.Ref() != "trusted/deploy" || len(v.Rejected) != 1 || !v.Rejected[0].Quarantined {
		t.Fatalf("when the incumbent returns: holder %s, rejected %+v", e.Ref(), v.Rejected)
	}

	// The incumbent's member renames the tool (here only its case): it keeps
	// the name, against its own earlier tool.
	v, _ = Compose(spec, []Tool{tool("trusted", "Deploy"), tool("other", "deploy")}, held)
	if e, _ := v.Lookup("Deploy"); e.Ref() != "trusted/Deploy" || len(v.Rejected) != 1 || v.Rejected[0].Ref != "other/deploy" {
		t.Fatalf("the incumbent's own rename: entries %v, rejected %+v", exposed(v), v.Rejected)
	}

	// An admin's resolution releases the name to the newcomer.
	for name, s := range map[string]Spec{
		"exclude":           {Naming: NamingFlat, Members: spec.Members, Exclude: []string{"trusted/deploy"}},
		"include":           {Naming: NamingFlat, Members: spec.Members, Include: []string{"other/*"}},
		"renaming override": {Naming: NamingFlat, Members: spec.Members, Overrides: map[string]*tenant.MCPToolOverride{"trusted/deploy": {Name: "trusted_deploy"}}},
		"hidden override":   {Naming: NamingFlat, Members: spec.Members, Overrides: map[string]*tenant.MCPToolOverride{"trusted/deploy": {Hidden: &[]bool{true}[0]}}},
		"member removed":    {Naming: NamingFlat, Members: []string{"other"}},
	} {
		v, _ := Compose(s, []Tool{tool("other", "deploy")}, held)
		if e, ok := v.Lookup("deploy"); !ok || e.Ref() != "other/deploy" || len(v.Rejected) != 0 {
			t.Fatalf("%s: entries %v, rejected %+v; want other/deploy to take the name", name, exposed(v), v.Rejected)
		}
	}
}
