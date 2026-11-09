package cassette

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// RefusedError is a cassette that still holds a credential-shaped value, an
// email, or a value the scrubber collected. It names the rule and the place,
// never the value. Nothing is written when it is returned.
type RefusedError struct {
	Findings []string
}

func (e *RefusedError) Error() string {
	return fmt.Sprintf("refusing to write the cassette: %d finding(s) after scrubbing: %s",
		len(e.Findings), strings.Join(e.Findings, "; "))
}

// verify scans a cassette's final bytes: credential shapes in the raw file
// and in every decoded string and member name (and in Base64 inside one),
// emails that are not placeholders, URLs whose credential parameters (by
// the built-in names and n's) hold a value that is not a placeholder, and
// any value the scrubber collected (ph nil: none known) in a string, a
// number, or a member name of recorded JSON (a literal in a name only where
// it stands alone, as the scrubber replaces it). The cassette's own names
// (its fields, header names, placeholder classes) and own values (format,
// notice, methods, rules) are checked for shapes, emails and URLs only: the
// scrubber does not write them, and a literal may spell one. A finding's
// path never shows a recorded member name that is itself a finding, and
// always shows the cassette's own names.
func verify(data []byte, ph *placeholders, n *nameRules) error {
	found := map[string]bool{}
	for _, f := range FindSecretShapes(string(data)) {
		found[fmt.Sprintf("%s in the file at byte %d", f.Rule, f.Offset)] = true
	}
	note := func(findings []string, at string) {
		for _, f := range findings {
			found[f+" at "+at] = true
		}
	}
	hidden := map[string]bool{}
	hide := func(name, parent string) bool {
		known, key := ph, "own:"+name
		if recordedPathRE.MatchString(parent) {
			key = "recorded:" + name
		} else {
			known = nil
		}
		h, ok := hidden[key]
		if !ok {
			h = len(checkName(name, known, n)) > 0
			hidden[key] = h
		}
		return h
	}
	_, err := walkJSON(data, walkOptions{hide: hide}, func(l jsonLeaf) []byte {
		own := ownValuePathRE.MatchString(l.path)
		switch {
		case l.name:
			note(checkName(*l.str, nil, n), l.path)
		case l.str != nil && own:
			note(checkString(*l.str, nil, n), l.path)
		case l.str != nil:
			note(checkString(*l.str, ph, n), l.path)
		case ph != nil && !own && ph.global(string(l.num)):
			found["scrub-incomplete:"+ph.className(string(l.num))+" at "+l.path] = true
		}
		return nil
	})
	if err != nil {
		return fmt.Errorf("verify: %w", err)
	}
	if ph != nil {
		docs, err := recordedJSON(data)
		if err != nil {
			return fmt.Errorf("verify: %w", err)
		}
		for _, d := range docs {
			if _, err := walkJSON(d.data, walkOptions{hide: hide, root: d.path}, func(l jsonLeaf) []byte {
				if l.name {
					note(checkName(*l.str, ph, n), l.path)
				}
				return nil
			}); err != nil {
				return fmt.Errorf("verify: %s: %w", d.path, err)
			}
		}
	}
	if len(found) == 0 {
		return nil
	}
	findings := make([]string, 0, len(found))
	for f := range found {
		findings = append(findings, f)
	}
	sort.Strings(findings)
	return &RefusedError{Findings: findings}
}

// jsonDocument is one recorded JSON document in a cassette file and its
// path there.
type jsonDocument struct {
	path string
	data json.RawMessage
}

// recordedJSON lists the documents whose member names the scrubber
// rewrites: MCP params, results and errors, and HTTP JSON bodies. It reads
// field names as Load does (case-insensitively).
func recordedJSON(data []byte) ([]jsonDocument, error) {
	type side struct {
		Body *struct {
			JSON json.RawMessage `json:"json"`
		} `json:"body"`
	}
	var doc struct {
		MCP *struct {
			Exchanges []struct {
				Params json.RawMessage `json:"params"`
				Result json.RawMessage `json:"result"`
				Error  json.RawMessage `json:"error"`
			} `json:"exchanges"`
		} `json:"mcp"`
		HTTP *struct {
			Exchanges []struct {
				Request  side `json:"request"`
				Response side `json:"response"`
			} `json:"exchanges"`
		} `json:"http"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, err
	}
	var out []jsonDocument
	add := func(path string, raw json.RawMessage) {
		if len(raw) > 0 && !bytes.Equal(raw, []byte("null")) {
			out = append(out, jsonDocument{path, raw})
		}
	}
	if doc.MCP != nil {
		for i, x := range doc.MCP.Exchanges {
			add(fmt.Sprintf("$.mcp.exchanges[%d].params", i), x.Params)
			add(fmt.Sprintf("$.mcp.exchanges[%d].result", i), x.Result)
			add(fmt.Sprintf("$.mcp.exchanges[%d].error", i), x.Error)
		}
	}
	if doc.HTTP != nil {
		for i, x := range doc.HTTP.Exchanges {
			for name, sd := range map[string]side{"request": x.Request, "response": x.Response} {
				if sd.Body != nil {
					add(fmt.Sprintf("$.http.exchanges[%d].%s.body.json", i, name), sd.Body.JSON)
				}
			}
		}
	}
	return out, nil
}

// recordedPathRE matches a path inside recorded JSON, where member names
// are vendor data the scrubber rewrites; ownValuePathRE a value of the
// cassette's own, which the scrubber never writes.
var (
	tokenAtRE      = regexp.MustCompile(`^scrubbed-token-[0-9]+@`)
	recordedPathRE = regexp.MustCompile(`^\$\.(?:mcp\.exchanges\[[0-9]+\]\.(?:params|result|error)|http\.exchanges\[[0-9]+\]\.(?:request|response)\.body\.json)(?:[.\[]|$)`)
	ownValuePathRE = regexp.MustCompile(`^\$\.(?:format|kind|notice|recorded_at|mcp\.protocol_version|mcp\.exchanges\[[0-9]+\]\.method|http\.exchanges\[[0-9]+\]\.request\.method)$|^\$\.(?:rules|scrubbed)(?:[.\[]|$)`)
)

// checkString names what one decoded string still holds.
func checkString(s string, ph *placeholders, n *nameRules) []string {
	return check(s, ph, n, false)
}

// checkName is checkString for a member name, where a literal survives
// only standing alone.
func checkName(s string, ph *placeholders, n *nameRules) []string {
	return check(s, ph, n, true)
}

func check(s string, ph *placeholders, n *nameRules, inName bool) []string {
	var out []string
	for _, f := range FindSecretShapes(s) {
		out = append(out, f.Rule)
	}
	out = append(out, urlFindings(s, n)...)
	for _, e := range emailRE.FindAllString(s, -1) {
		if !endsInPlaceholderEmail(e) {
			out = append(out, "email")
			break
		}
	}
	if ph != nil {
		for _, c := range ph.survivors(s, inName) {
			out = append(out, "scrub-incomplete:"+c)
		}
	}
	if u, err := url.PathUnescape(s); err == nil && u != s {
		for _, f := range check(u, ph, n, inName) {
			out = append(out, f+" (escaped)")
		}
	}
	if dec, _, ok := decodeBase64(s); ok {
		for _, f := range FindSecretShapes(string(dec)) {
			out = append(out, f.Rule+" (base64)")
		}
		if text, _, ok := decodeBase64Text(s); ok {
			for _, f := range check(text, ph, n, inName) {
				out = append(out, f+" (base64)")
			}
		}
	}
	return out
}

// endsInPlaceholderEmail accepts a placeholder email, also when the email
// pattern glued text before it (a query's "a+b@x" is "a b@x": the scrubber
// replaced "b@x", and the literal + now joins "a" to the placeholder), and
// a token placeholder before an @: the scrubbed user info of a URL
// (https://scrubbed-token-1@hooks.example.com) is no email.
func endsInPlaceholderEmail(e string) bool {
	if tokenAtRE.MatchString(e) {
		return true
	}
	local, ok := strings.CutSuffix(e, "@example.com")
	at := strings.LastIndex(local, "user-")
	return ok && at >= 0 && digitsRE.MatchString(local[at+len("user-"):]) && (at == 0 || !isAlnum(local[at-1]))
}

// Save scrubs c, verifies the result and only then writes it to path,
// atomically. When verification refuses, nothing is written: no file and no
// temporary file. c is scrubbed in place either way.
func Save(path string, c *Cassette, cfg ScrubConfig) error {
	if err := cfg.Validate(); err != nil {
		return err
	}
	if err := ValidateTestAccount(c.TestAccount); err != nil {
		return err
	}
	c.Format, c.Notice = Format, Notice
	c.Rules = mergeRules(c.Rules, &Rules{SecretParams: cfg.SecretParams, SecretKeys: cfg.SecretKeys})
	ph, err := scrub(c, cfg)
	if err != nil {
		return fmt.Errorf("scrub: %w", err)
	}
	if err := c.Validate(); err != nil {
		return fmt.Errorf("scrubbed cassette: %w", err)
	}
	data, err := marshal(c)
	if err != nil {
		return err
	}
	if err := verify(data, ph, compileRules(c.Rules)); err != nil {
		return err
	}
	return writeAtomic(path, data)
}

// Check verifies a cassette already on disk: it loads strictly and refuses
// any credential shape, non-placeholder email, or URL credential (by the
// built-in names and the cassette's own rules) left in it.
func Check(data []byte) error {
	c, err := Load(data)
	if err != nil {
		return err
	}
	return verify(data, nil, compileRules(c.Rules))
}

func marshal(c *Cassette) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(c); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// writeAtomic writes data through a temporary file in the target directory
// and a rename, so a reader never sees half a cassette.
func writeAtomic(path string, data []byte) (err error) {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".cassette-*.tmp")
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			os.Remove(tmp.Name())
		}
	}()
	if _, err = tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	if err = os.Chmod(tmp.Name(), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}
