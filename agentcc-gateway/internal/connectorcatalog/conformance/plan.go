package conformance

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io/fs"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

// StaticOrg is the gateway's mcp.static.org_id in a run: every K4 (stdio)
// case's server is an operator YAML server, which serves only this org.
const StaticOrg = "org-cn8-static"

// undeclaredTool is the tool the mock serves beside a provider's declared
// tools when it has no recording, so the provider's default posture is
// judged on a tool the manifest does not name.
const undeclaredTool = "cn8_undeclared"

// InboundEra is the protocol revision the harness calls the gateway under:
// it opens each session with it and sends it as MCP-Protocol-Version, so
// every record must carry it.
const InboundEra = "2025-06-18"

// envProbeTool is agentcc-mcpmock's stdio-only tool that names the variables
// its process sees (never their values).
const envProbeTool = "env_names"

// Catalog is one catalog directory to plan cases for.
type Catalog struct {
	Label string // names the catalog in case ids
	FS    fs.FS
}

// Plan is every case of a run.
type Plan struct {
	Harness    string  `json:"harness"`
	StaticOrg  string  `json:"static_org"`
	InboundEra string  `json:"inbound_era"`
	Cases      []*Case `json:"cases"`
}

// VirtualServer is one virtual server a case's org config pushes.
type VirtualServer struct {
	Slug string `json:"slug"`
	ID   string `json:"id"`
}

// Case is one provider through the gateway: its mock upstream, the org
// config add-from-catalog would push for it, and the rows that judge it.
type Case struct {
	ID       string `json:"id"`  // <catalog>/<app>/<provider>
	Ref      string `json:"ref"` // catalog_ref, <app>/<provider>@<version>
	Kind     string `json:"kind"`
	ServerID string `json:"server_id"` // the server's id, and the mock's name
	Org      string `json:"org"`
	// Skip is why the whole case does not run; its one row says so.
	Skip string `json:"skip,omitempty"`

	Transport string `json:"transport,omitempty"` // http, sse or stdio
	// Era is the era every message the gateway sends must carry; "" is any
	// era the gateway serves (the mock speaks them all).
	Era string `json:"era,omitempty"`
	// PinEra is the org config's era: the manifest's pin, "" for auto.
	PinEra       string            `json:"pin_era,omitempty"`
	Path         string            `json:"path,omitempty"` // the path the gateway posts to
	Rest         string            `json:"rest,omitempty"` // appended to the mock's base URL
	URLVariables map[string]string `json:"url_variables,omitempty"`
	// MockArgs are agentcc-mcpmock's flags for the case, --fixtures aside.
	MockArgs   []string   `json:"mock_args,omitempty"`
	Credential Credential `json:"credential"`
	// DefaultPosture is the provider's posture for a tool it does not
	// declare.
	DefaultPosture string  `json:"default_posture,omitempty"`
	Tools          []*Tool `json:"tools,omitempty"`
	// EnvProbe is the exposed name of the stdio mock's env_names tool.
	EnvProbe  string   `json:"env_probe,omitempty"`
	Recording string   `json:"recording,omitempty"`
	Drift     []string `json:"drift,omitempty"`
	// Posture is the virtual server add-from-catalog would push: its access
	// rule refuses every tool the gateway must refuse. Audit is observation
	// scaffolding, never pushed by add-from-catalog: the same member with no
	// rule, so the whole served set and its annotations are visible.
	Posture VirtualServer `json:"posture"`
	Audit   VirtualServer `json:"audit"`
	Rows    []Row         `json:"rows"`
	// Fixture is the mock's tools.json; FixturesSHA256 is its sha256.
	Fixture        json.RawMessage `json:"fixture,omitempty"`
	FixturesSHA256 string          `json:"fixtures_sha256,omitempty"`

	recordingEra string // the cassette's protocol version
}

// Build plans a case for every provider of every catalog, in catalog, app
// and provider order. Each catalog must load cleanly (L0 and L1).
func Build(catalogs []Catalog) (*Plan, error) {
	plan := &Plan{Harness: HarnessVersion, StaticOrg: StaticOrg, InboundEra: InboundEra}
	ids := map[string]bool{}
	for _, c := range catalogs {
		cat, err := manifest.LoadDir(c.FS)
		if err != nil {
			return nil, fmt.Errorf("catalog %s: %w", c.Label, err)
		}
		if err := manifest.CheckL1(cat); err != nil {
			return nil, fmt.Errorf("catalog %s: L1: %w", c.Label, err)
		}
		for _, app := range cat.Apps {
			for _, p := range app.Providers {
				cs, err := planCase(c, p, len(plan.Cases)+1, ids)
				if err != nil {
					return nil, fmt.Errorf("catalog %s: %s/%s: %w", c.Label, p.App, p.ID, err)
				}
				plan.Cases = append(plan.Cases, cs)
			}
		}
	}
	if len(plan.Cases) == 0 {
		return nil, fmt.Errorf("the catalogs hold no provider: L3 has nothing to run")
	}
	return plan, nil
}

func planCase(c Catalog, p *manifest.Provider, n int, ids map[string]bool) (*Case, error) {
	cs := &Case{
		ID:  c.Label + "/" + p.App + "/" + p.ID,
		Ref: p.App + "/" + p.ID + "@" + p.Version, Kind: p.Kind,
		ServerID: serverID(p.App, p.ID, ids),
	}
	if p.Kind == manifest.KindPartner {
		cs.Skip = waitPartner
		cs.Rows = []Row{{ID: rowPartner, Skip: waitPartner}}
		return cs, nil
	}
	cs.Org = fmt.Sprintf("org-cn8-%d", n)
	cs.Transport = "http"
	if p.Transport.Kind == "stdio" {
		cs.Org, cs.Transport = StaticOrg, "stdio"
	} else if p.Transport.Variant == "sse" {
		cs.Transport = "sse"
	}
	slug := strings.ReplaceAll(cs.ServerID, "_", "-")
	cs.Posture = VirtualServer{Slug: slug + "-posture", ID: vsID(cs.ID + "#posture")}
	cs.Audit = VirtualServer{Slug: slug + "-audit", ID: vsID(cs.ID + "#audit")}
	if err := cs.planTools(c.FS, p); err != nil {
		return nil, err
	}
	if err := cs.planTransport(p); err != nil {
		return nil, err
	}
	cs.planRows(p)
	return cs, nil
}

// planTools decides the served tools: the recording's when the provider has
// a cassette, else its declared tools plus one undeclared tool.
func (cs *Case) planTools(fsys fs.FS, p *manifest.Provider) error {
	rec, err := readRecording(fsys, p.App, p.ID)
	if err != nil {
		return err
	}
	if rec != nil {
		cs.Tools, cs.Recording, cs.Drift = rec.Tools, rec.File, drift(p, rec.Tools)
		cs.recordingEra = rec.Era
	} else {
		if cs.Tools, err = declaredTools(p); err != nil {
			return err
		}
		extra := &Tool{Name: undeclaredTool, Hints: map[string]bool{}, Arguments: json.RawMessage(`{}`),
			def: json.RawMessage(`{"name":"` + undeclaredTool + `","inputSchema":{"type":"object"}}`)}
		cs.Tools = append(cs.Tools, extra)
	}
	for _, t := range cs.Tools {
		if t.Name == undeclaredTool && rec == nil && t.Declared {
			return fmt.Errorf("the manifest declares %s, the name L3 gives its undeclared tool", undeclaredTool)
		}
		if t.Name == envProbeTool && p.Transport.Kind == "stdio" {
			return fmt.Errorf("a stdio provider serves %s, which agentcc-mcpmock adds to every stdio list", envProbeTool)
		}
		if len(t.result) == 0 {
			// A result of its own, so the allowed-call row can tell the
			// upstream's answer reached the caller unchanged.
			t.result = json.RawMessage(`{"content":[{"type":"text","text":` + quote("cn8 L3 result of "+t.Name) + `}]}`)
		}
	}
	decide(cs.ServerID, p, cs.Tools)
	cs.DefaultPosture = p.Tools.DefaultPosture
	if p.Transport.Kind == "stdio" {
		cs.EnvProbe = vserver.EncodePrefixed(cs.ServerID, envProbeTool)
	}
	defs := make([]json.RawMessage, 0, len(cs.Tools))
	for _, t := range cs.Tools {
		def, err := t.fixture()
		if err != nil {
			return err
		}
		defs = append(defs, def)
	}
	raw, err := json.Marshal(map[string]any{"tools": defs})
	if err != nil {
		return err
	}
	sum := sha256.Sum256(raw)
	cs.Fixture, cs.FixturesSHA256 = raw, fmt.Sprintf("%x", sum)
	return nil
}

// planTransport decides the endpoint, the era and the credential.
func (cs *Case) planTransport(p *manifest.Provider) error {
	era, err := upstreamEra(p, cs.recordingEra)
	if err != nil {
		return err
	}
	cs.Era = era
	if p.Transport.Era != "" && p.Transport.Era != "auto" {
		cs.PinEra = p.Transport.Era
	}
	mockEra := era
	if mockEra == "" {
		mockEra = "dual"
	}
	cred, credRows := placeCredential(cs.Ref, p)
	cs.Credential = cred
	cs.Rows = append(cs.Rows, credRows...)
	if cs.Transport == "stdio" {
		cs.MockArgs = []string{"--mode", "stdio", "--era", mockEra}
		return nil
	}
	ep, err := renderEndpoint(p)
	if err != nil {
		return err
	}
	cs.Path, cs.Rest, cs.URLVariables = ep.Path, ep.Rest, ep.OrgVariables
	cs.Rows = append(cs.Rows, ep.Skips...)
	cs.MockArgs = append([]string{"--era", mockEra, "--path", ep.Path}, cred.MockArgs...)
	return nil
}

// planRows adds the rows every running case has, and the named skips its
// manifest calls for.
func (cs *Case) planRows(p *manifest.Provider) {
	rows := []Row{{ID: rowListAudit}, {ID: rowListPosture}, {ID: rowEra}, {ID: rowNotForwarded}, {ID: rowRecords}}
	if len(cs.Credential.Values) > 0 {
		rows = append(rows, Row{ID: rowNotLogged}) // a case that places no credential has nothing to leak
	}
	ask, generic := false, false
	for _, t := range cs.Tools {
		rows = append(rows, Row{ID: prefixAnnotations + t.Exposed})
		if t.Denied {
			rows = append(rows, Row{ID: prefixDenied + t.Exposed})
		} else {
			rows = append(rows, Row{ID: prefixAllowed + t.Exposed})
		}
		ask = ask || t.Posture == manifest.PostureAsk
		generic = generic || t.Generic
	}
	if cs.Recording != "" {
		rows = append(rows, Row{ID: rowDrift})
	} else if len(p.AllTools()) == 0 {
		rows = append(rows, Row{ID: "tools.declared", Skip: waitNoTools})
	}
	skips := []struct {
		on     bool
		id, by string
	}{
		{ask, rowAsk, waitApprovals},
		{generic, rowGeneric, waitGeneric},
		{len(p.RateLimits) > 0, rowRateLimits, waitRateLimits},
		{p.Tools.Snapshot != nil, rowPins, waitPins},
		{slices.Contains(p.AccountModes, "user"), rowUserMode, waitUserMode},
		{p.Kind == manifest.KindREST, rowK2Runtime, waitK2Runtime},
	}
	for _, s := range skips {
		if s.on {
			rows = append(rows, Row{ID: s.id, Skip: s.by})
		}
	}
	cs.Rows = append(cs.Rows, rows...)
	slices.SortFunc(cs.Rows, func(a, b Row) int { return strings.Compare(a.ID, b.ID) })
}

// serverID is the case's server id: <app>_<provider>, kept to the org
// config's id pattern and made unique within the run.
func serverID(app, provider string, ids map[string]bool) string {
	base := strings.ReplaceAll(app+"_"+provider, "-", "_")
	if len(base) > 40 {
		base = base[:40]
	}
	id := base
	for i := 2; ids[id]; i++ {
		id = fmt.Sprintf("%s_%d", base, i)
	}
	ids[id] = true
	return id
}

// crockford is the ULID alphabet.
const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

// vsID is a stable vs_<ULID> for a seed, as the control plane would assign.
func vsID(seed string) string {
	sum := sha256.Sum256([]byte(seed))
	var b strings.Builder
	b.WriteString("vs_")
	for i := 0; i < 26; i++ {
		b.WriteByte(crockford[int(sum[i])%32])
	}
	return b.String()
}

// Rehydrate restores what plan.json does not carry: each tool's listed
// definition and canned result, from the case's fixture.
func Rehydrate(plan *Plan) (*Plan, error) {
	for _, cs := range plan.Cases {
		if cs.Skip != "" {
			continue
		}
		var fix struct {
			Tools []json.RawMessage `json:"tools"`
		}
		if err := json.Unmarshal(cs.Fixture, &fix); err != nil {
			return nil, fmt.Errorf("case %s: fixture: %w", cs.ID, err)
		}
		byName := map[string]map[string]json.RawMessage{}
		for _, raw := range fix.Tools {
			var obj map[string]json.RawMessage
			if err := json.Unmarshal(raw, &obj); err != nil {
				return nil, fmt.Errorf("case %s: fixture: %w", cs.ID, err)
			}
			var name string
			_ = json.Unmarshal(obj["name"], &name)
			byName[name] = obj
		}
		for _, t := range cs.Tools {
			obj, ok := byName[t.Name]
			if !ok {
				return nil, fmt.Errorf("case %s: the fixture has no tool %s", cs.ID, t.Name)
			}
			t.result = obj["result"]
			delete(obj, "result")
			def, err := json.Marshal(obj)
			if err != nil {
				return nil, err
			}
			t.def = def
		}
	}
	return plan, nil
}
