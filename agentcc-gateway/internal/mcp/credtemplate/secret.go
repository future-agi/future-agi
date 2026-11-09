package credtemplate

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
)

// Secret is a pushed MCP credential secret (the org-config contract's
// MCPCredentialSecret, tenant.MCPCredentialSecret) with the header of the
// credential reference it belongs to.
type Secret struct {
	Type   string // bearer or api_key
	Header string // the secret's own header, as pushed
	// ReferenceHeader is the server credential's header
	// (MCPServerCredential.header), which a header placement uses when the
	// secret names none.
	ReferenceHeader string
	In              string // "", header, query or url_variable (AM-C9)
	Name            string
	Value           string
	// Parts are the credential's other secret parts (AM-C20); In, Name and
	// Value above are its first.
	Parts []SecretPart
}

// SecretPart is one more secret part of a pushed credential
// (MCPCredentialSecretPart).
type SecretPart struct {
	In    string
	Name  string
	Value string
}

// SecretHeader is the header a pushed secret with no placement goes in, as
// the gateway has always sent it (MC.4): Authorization for a bearer
// credential or when neither the secret nor its reference names a header;
// otherwise the secret's header, or failing that the reference's. A value
// in Authorization goes as "Bearer <value>". tenant.MCPSecretHeader is this
// rule.
func SecretHeader(typ, header, referenceHeader string) string {
	if header == "" {
		header = referenceHeader
	}
	if typ == "bearer" || header == "" {
		return "Authorization"
	}
	return header
}

// Applies reports whether a server's pushed secret is placed by a
// template: it states a placement (in, name or parts), or the server was
// added from the catalog, whose provider may declare more parts. A secret
// with none of these keeps the gateway's header path (SecretHeader), whose
// behaviour and fingerprint do not change.
func Applies(sec Secret, catalogRef string) bool {
	return sec.In != "" || sec.Name != "" || len(sec.Parts) > 0 || catalogRef != ""
}

// Parts are the pushed parts of sec, its own first, each checked: a
// placement the gateway cannot apply exactly as stated is refused, never
// sent elsewhere (AM-C20 item 2). The rules:
//   - type is bearer or api_key;
//   - in unset is today's header placement (SecretHeader), and so is in
//     header with no name; a name there must be that header;
//   - in header with a name places it in that header; the secret's header,
//     else its reference's, when set, is the same header; a bearer goes only
//     in Authorization;
//   - in query or url_variable is an api_key with no header;
//   - each further part is a header, query or url_variable part, never in
//     Authorization or Proxy-Authorization (only the first part has a type);
//   - every name matches its pattern (an HTTP token of at most 64
//     characters that the gateway does not set itself, queryName, varName)
//     and every value is non-empty, at most MaxValueChars characters, with
//     no control character and no surrounding space.
//
// A value in Authorization goes as "Bearer <value>", as it always has.
func Parts(sec Secret) ([]Part, error) {
	if sec.Type != "bearer" && sec.Type != "api_key" {
		return nil, refuse("type", "is not bearer or api_key")
	}
	first, err := firstPart(sec)
	if err != nil {
		return nil, err
	}
	if len(sec.Parts) > MaxParts-1 {
		return nil, refuse("parts", "has more than %d entries: a credential has at most %d parts", MaxParts-1, MaxParts)
	}
	parts := []Part{first}
	for i, sp := range sec.Parts {
		at := fmt.Sprintf("parts[%d].", i)
		p := Part{In: In(sp.In), Name: sp.Name, Value: sp.Value}
		if p.In == InHeader && (strings.EqualFold(p.Name, "Authorization") || strings.EqualFold(p.Name, "Proxy-Authorization")) {
			return nil, refuse(at+"name", "only the credential's first part, which has a type, goes in %s", p.Name)
		}
		if field, reason := checkPart(p); field != "" {
			return nil, refuse(at+field, "%s", reason)
		}
		parts = append(parts, p)
	}
	return parts, nil
}

func firstPart(sec Secret) (Part, error) {
	var p Part
	nameField := "name"
	switch {
	case sec.In == "" || sec.In == string(InHeader) && sec.Name == "":
		// No placement, or in header with no name: the header the gateway
		// has always sent it in (in header names its header in header).
		header := SecretHeader(sec.Type, sec.Header, sec.ReferenceHeader)
		if sec.Name != "" && !strings.EqualFold(sec.Name, header) {
			return Part{}, refuse("name", "is not the header the credential is sent in (%s): a header placement's name is its header", header)
		}
		p, nameField = Part{In: InHeader, Name: header, Value: sec.Value}, "header"
	case sec.In == string(InHeader):
		if sec.Header != "" && !strings.EqualFold(sec.Header, sec.Name) {
			return Part{}, refuse("header", "is not name: in header carries its header in both")
		}
		if sec.Header == "" && sec.ReferenceHeader != "" && !strings.EqualFold(sec.ReferenceHeader, sec.Name) {
			return Part{}, refuse("name", "is not the credential's header (%s)", sec.ReferenceHeader)
		}
		if sec.Type == "bearer" && !strings.EqualFold(sec.Name, "Authorization") {
			return Part{}, refuse("name", "is not Authorization: a bearer credential goes only in Authorization")
		}
		p = Part{In: InHeader, Name: sec.Name, Value: sec.Value}
	case sec.In == string(InQuery) || sec.In == string(InURLVariable):
		if sec.Type != "api_key" {
			return Part{}, refuse("type", "is not api_key: only an api_key credential goes in a query parameter or a URL variable")
		}
		if sec.Header != "" {
			return Part{}, refuse("header", "is set, but in %s places the credential by name, never in a header", sec.In)
		}
		if sec.Name == "" {
			return Part{}, refuse("name", "is required: in %s names its parameter or variable in name", sec.In)
		}
		p = Part{In: In(sec.In), Name: sec.Name, Value: sec.Value}
	default:
		return Part{}, refuse("in", "is not header, query or url_variable")
	}
	if p.In == InHeader && strings.EqualFold(p.Name, "Authorization") {
		p.Name, p.Prefix = "Authorization", "Bearer "
	}
	if field, reason := checkPart(p); field != "" {
		if field == "name" {
			field = nameField
		}
		return Part{}, refuse(field, "%s", reason)
	}
	return p, nil
}

// PlacementVersion stands for sec's placement (in, name and parts, values
// included) in a server's fingerprint, without holding a value: "" when
// sec states none, so a fingerprint built from type, header and value
// alone does not change for a secret pushed as before (AM-C20 item 2);
// otherwise a digest that changes with any of them, a rotated part
// included.
func PlacementVersion(sec Secret) string {
	if sec.In == "" && sec.Name == "" && len(sec.Parts) == 0 {
		return ""
	}
	fields := []string{"in=" + sec.In, "name=" + sec.Name}
	for _, p := range sec.Parts {
		fields = append(fields, "part", p.In, p.Name, p.Value)
	}
	raw, _ := json.Marshal(fields)
	sum := sha256.Sum256(raw)
	return "placement:" + hex.EncodeToString(sum[:])
}
