package conformance

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Credential is the test credential the harness gives a case and what the
// mock must see of it.
type Credential struct {
	// Secret is the push-only secret of the server's credential
	// (MCPCredentialSecret, AM-C9's form), for an http case.
	Secret *Secret `json:"secret,omitempty"`
	// Env is a stdio child's declared environment: each name the provider
	// declares, with a test value.
	Env map[string]string `json:"env,omitempty"`
	// Expect are the credential headers the mock must receive on every
	// request.
	Expect []Placement `json:"expect,omitempty"`
	// Values are the test values the case sets, which no log, record or
	// answer may hold.
	Values []string `json:"values,omitempty"`
	// MockArgs are the agentcc-mcpmock flags that witness (or require) the
	// placement.
	MockArgs []string `json:"mock_args,omitempty"`
}

// Secret is the pushed MCPCredentialSecret: its first part, and any other
// secret part in Parts (AM-C20).
type Secret struct {
	Type   string       `json:"type"`
	Header string       `json:"header,omitempty"`
	In     string       `json:"in,omitempty"`
	Name   string       `json:"name,omitempty"`
	Value  string       `json:"value"`
	Parts  []SecretPart `json:"parts,omitempty"`
}

// SecretPart is one more secret part of a pushed credential
// (MCPCredentialSecret.parts).
type SecretPart struct {
	In    string `json:"in"`
	Name  string `json:"name"`
	Value string `json:"value"`
}

// Placement is one credential header the mock must receive.
type Placement struct {
	Row    string `json:"row"`    // the row that judges it
	Header string `json:"header"` // lower case
	SHA256 string `json:"sha256"` // of the header's whole value
	// Auth: the mock reports this header as auth (its --auth-header), not
	// under credentials.
	Auth bool `json:"auth"`
}

// testValue is a case's fake credential: stable, unique per case and part,
// and shaped like no provider's key.
func testValue(ref, part string) string {
	sum := sha256.Sum256([]byte(ref + "\x00" + part))
	return "cn8-l3-" + hex.EncodeToString(sum[:12])
}

func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// placeCredential decides the case's credential from the provider's auth:
// what L3 pushes, a row for each credential part, and a named skip for each
// part it does not check. The gateway takes a catalog provider's credential
// whole (CN7: every declared secret part pushed, the declared constants
// added from the catalog, anything less refused), so L3 pushes every secret
// part or none: one part it cannot push or witness holds back the whole
// credential, and each part's row says why.
func placeCredential(ref string, p *manifest.Provider) (Credential, []Row) {
	var cred Credential
	var rows []Row
	if p.Transport.Kind == "stdio" {
		for _, name := range sortedKeys(p.Transport.Env) {
			if cred.Env == nil {
				cred.Env = map[string]string{}
			}
			v := testValue(ref, "env:"+name)
			cred.Env[name] = v
			cred.Values = append(cred.Values, v)
			rows = append(rows, Row{ID: prefixEnv + name})
		}
		if len(cred.Env) > 0 {
			rows = append(rows, Row{ID: "credential.env_value", Skip: waitEnvValue})
		}
		return cred, rows
	}
	switch p.Auth.Kind {
	case manifest.AuthOAuth2, manifest.AuthMCPOAuth:
		rows = append(rows, Row{ID: "credential.oauth", Skip: waitOAuth})
	case manifest.AuthBasic:
		rows = append(rows, Row{ID: "credential.basic", Skip: waitBasic})
	case manifest.AuthServiceAccount:
		rows = append(rows, Row{ID: "credential.service_account", Skip: waitServiceAccount})
	case manifest.AuthAPIKey:
		return placeAPIKey(ref, p.Auth.CredentialParts)
	}
	return cred, rows
}

// placeAPIKey places an api_key credential whole: every secret header part
// pushed (the first as the secret, the rest in its parts) and witnessed by
// sha256, every constant header part witnessed with the catalog's value; or,
// when any part cannot be, nothing, each row naming why.
func placeAPIKey(ref string, parts []manifest.CredentialPart) (Credential, []Row) {
	rowOf := func(part manifest.CredentialPart) string { return "credential." + part.In + "." + part.Name }
	held, secrets := "", 0
	reasons := make([]string, len(parts))
	for i, part := range parts {
		reasons[i] = unplaceable(part)
		if reasons[i] == "" && part.Secret && secrets > 0 && strings.EqualFold(part.Name, "Authorization") {
			reasons[i] = waitLaterAuthorization()
		}
		if reasons[i] != "" && held == "" {
			held = part.Name
		}
		if part.Secret {
			secrets++
		}
	}
	if held == "" && secrets == 0 {
		held = "(no secret part)"
		for i, part := range parts {
			reasons[i] = waitNoSecretPart(part.Name)
		}
	}
	var cred Credential
	rows := make([]Row, 0, len(parts))
	for i, part := range parts {
		switch {
		case reasons[i] != "":
			rows = append(rows, Row{ID: rowOf(part), Skip: reasons[i]})
		case held != "":
			rows = append(rows, Row{ID: rowOf(part), Skip: waitHeldBack(part.Name, held)})
		default:
			cred.placePart(ref, part, rowOf(part))
			rows = append(rows, Row{ID: rowOf(part)})
		}
	}
	return cred, rows
}

// unplaceable is why L3 cannot push or witness one credential part, or "".
func unplaceable(part manifest.CredentialPart) string {
	switch {
	case part.In == "query":
		return waitQuery(part.Name)
	case part.In == "url_variable":
		return waitURLVariable(part.Name)
	case !part.Secret && part.Value == "":
		return waitCustomerSet(part.Name)
	case !part.Secret:
		return "" // a constant header: the gateway adds it, the mock witnesses it
	case strings.EqualFold(part.Name, "Authorization") && part.Prefix != "Bearer ":
		return waitPrefix(part.Name, part.Prefix)
	case !strings.EqualFold(part.Name, "Authorization") && part.Prefix != "":
		return waitPrefix(part.Name, part.Prefix)
	}
	return ""
}

// placePart places one header part unplaceable accepts: the first secret
// part as the pushed secret (Authorization as a Bearer token, any other
// header as its raw value, the mock's auth header), a later secret part in
// the secret's parts, and a constant part, which is never pushed, as the
// header the gateway adds from the catalog. The mock witnesses each of the
// latter two by sha256 (--credential-header).
func (c *Credential) placePart(ref string, part manifest.CredentialPart, row string) {
	header := strings.ToLower(part.Name)
	if !part.Secret {
		c.Expect = append(c.Expect, Placement{Row: row, Header: header, SHA256: sha256Hex(part.Value)})
		c.MockArgs = append(c.MockArgs, "--credential-header", part.Name)
		return
	}
	value := testValue(ref, "header:"+part.Name)
	c.Values = append(c.Values, value)
	switch {
	case c.Secret != nil:
		c.Secret.Parts = append(c.Secret.Parts, SecretPart{In: "header", Name: part.Name, Value: value})
		c.Expect = append(c.Expect, Placement{Row: row, Header: header, SHA256: sha256Hex(value)})
		c.MockArgs = append(c.MockArgs, "--credential-header", part.Name)
	case strings.EqualFold(part.Name, "Authorization"):
		c.Secret = &Secret{Type: "bearer", Value: value}
		c.Expect = append(c.Expect, Placement{Row: row, Header: "authorization", SHA256: sha256Hex("Bearer " + value), Auth: true})
		c.MockArgs = append(c.MockArgs, "--auth", "bearer:"+value)
	default:
		c.Secret = &Secret{Type: "api_key", Header: part.Name, In: "header", Name: part.Name, Value: value}
		c.Expect = append(c.Expect, Placement{Row: row, Header: header, SHA256: sha256Hex(value), Auth: true})
		c.MockArgs = append(c.MockArgs, "--auth-header", part.Name)
	}
}

// String keeps a Credential's test values out of %v output.
func (c Credential) String() string {
	return fmt.Sprintf("credential{%d header(s), %d env}", len(c.Expect), len(c.Env))
}
