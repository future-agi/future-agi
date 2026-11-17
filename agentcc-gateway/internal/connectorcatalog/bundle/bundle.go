// Package bundle compiles a connector catalog into dist/catalog.json, schema
// connector-catalog-bundle/v1 (CONNECTORS-PLAN §2.5), and reads one back.
//
// A bundle holds every app, provider and platform record exactly as its
// file states it, as JSON, with every object's keys in RFC 8785 order. A
// rest provider's tools.generated.json is folded into its tools.items,
// after its curated tools, and its tools.generated key dropped: the bundle
// schema's provider is the manifest's provider, which has no other place
// for them. Its sha256 is the SHA-256, in lowercase hex, of the RFC 8785
// (JCS) form of {"apps": [...], "platforms": [...], "providers": [...]}, so
// a reader recomputes it from the file alone; in JavaScript that is the
// SHA-256 of JSON.stringify of that object with its keys sorted.
package bundle

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"regexp"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
)

// Schema is the bundle's schema identifier.
const Schema = "connector-catalog-bundle/v1"

// Bundle is one dist/catalog.json. Apps, Providers and Platforms are
// canonjson values in bundle form: apps by id, providers by app and id,
// platforms by id.
type Bundle struct {
	Version      string // YYYY.MM.DD.N
	SHA256       string // Digest of the three record lists
	SourceCommit string // the git commit the records were built from
	Apps         []any
	Providers    []any
	Platforms    []any
}

var (
	versionPattern = regexp.MustCompile(`^([0-9]{4}[.](0[1-9]|1[0-2])[.](0[1-9]|[12][0-9]|3[01]))[.](0|[1-9][0-9]{0,3})$`)
	sha256Pattern  = regexp.MustCompile(`^[0-9a-f]{64}$`)
	commitPattern  = regexp.MustCompile(`^[0-9a-f]{40}$`)
)

// Digest is the bundle sha256 of three record lists (see the package doc).
func Digest(apps, providers, platforms []any) (string, error) {
	data, err := canonjson.Canonical(canonjson.Object{
		{Key: "apps", Value: orEmpty(apps)}, {Key: "providers", Value: orEmpty(providers)}, {Key: "platforms", Value: orEmpty(platforms)},
	})
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

func orEmpty(v []any) []any {
	if v == nil {
		return []any{}
	}
	return v
}

// Encode renders the bundle as dist/catalog.json: Prettier-stable JSON with
// the envelope's keys in schema order.
func (b *Bundle) Encode() ([]byte, error) {
	if err := b.checkEnvelope(); err != nil {
		return nil, err
	}
	return canonjson.Pretty(canonjson.Object{
		{Key: "schema", Value: Schema},
		{Key: "version", Value: b.Version},
		{Key: "sha256", Value: b.SHA256},
		{Key: "sourceCommit", Value: b.SourceCommit},
		{Key: "apps", Value: orEmpty(b.Apps)},
		{Key: "providers", Value: orEmpty(b.Providers)},
		{Key: "platforms", Value: orEmpty(b.Platforms)},
	})
}

func (b *Bundle) checkEnvelope() error {
	var errs []error
	if !versionPattern.MatchString(b.Version) {
		errs = append(errs, fmt.Errorf("version %q is not YYYY.MM.DD.N", b.Version))
	} else if _, err := time.Parse("2006.01.02", versionPattern.FindStringSubmatch(b.Version)[1]); err != nil {
		errs = append(errs, fmt.Errorf("version %q does not name a calendar date", b.Version))
	}
	if !commitPattern.MatchString(b.SourceCommit) {
		errs = append(errs, fmt.Errorf("sourceCommit %q is not a 40-character git commit", b.SourceCommit))
	}
	want, err := Digest(b.Apps, b.Providers, b.Platforms)
	switch {
	case err != nil:
		errs = append(errs, err)
	case !sha256Pattern.MatchString(b.SHA256):
		errs = append(errs, fmt.Errorf("sha256 %q is not 64 lowercase hex digits", b.SHA256))
	case b.SHA256 != want:
		errs = append(errs, fmt.Errorf("sha256 %s does not match its records, whose digest is %s", b.SHA256, want))
	}
	return errors.Join(errs...)
}

// members are connector-catalog-bundle/v1's members, in schema order.
var members = []string{"schema", "version", "sha256", "sourceCommit", "apps", "providers", "platforms"}

// Decode reads a dist/catalog.json strictly: exactly the seven members of
// connector-catalog-bundle/v1, each well formed, and a sha256 that matches
// the records. Call Catalog to check the records themselves.
func Decode(data []byte) (*Bundle, error) {
	v, err := canonjson.Parse(data)
	if err != nil {
		return nil, fmt.Errorf("bundle: %w", err)
	}
	obj, ok := v.(canonjson.Object)
	if !ok {
		return nil, errors.New("bundle: must be a JSON object")
	}
	for _, m := range obj {
		if !contains(members, m.Key) {
			return nil, fmt.Errorf("bundle: member %q is not in connector-catalog-bundle/v1", m.Key)
		}
	}
	for _, key := range members {
		if _, ok := obj.Get(key); !ok {
			return nil, fmt.Errorf("bundle: has no %s member", key)
		}
	}
	var errs []error
	str := func(key string) string {
		s, ok := mustGet(obj, key).(string)
		if !ok {
			errs = append(errs, fmt.Errorf("bundle: %s must be a string", key))
		}
		return s
	}
	list := func(key string) []any {
		l, ok := mustGet(obj, key).([]any)
		if !ok {
			errs = append(errs, fmt.Errorf("bundle: %s must be an array", key))
		}
		return l
	}
	if s := str("schema"); s != Schema {
		errs = append(errs, fmt.Errorf("bundle: schema is %q, not %s", s, Schema))
	}
	b := &Bundle{
		Version: str("version"), SHA256: str("sha256"), SourceCommit: str("sourceCommit"),
		Apps: list("apps"), Providers: list("providers"), Platforms: list("platforms"),
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	if err := b.checkEnvelope(); err != nil {
		return nil, fmt.Errorf("bundle: %w", err)
	}
	return b, nil
}

func contains(list []string, s string) bool {
	for _, e := range list {
		if e == s {
			return true
		}
	}
	return false
}

func mustGet(o canonjson.Object, key string) any {
	v, _ := o.Get(key)
	return v
}

// ReadFile decodes the bundle at path in fsys, or returns nil and no error
// when there is none.
func ReadFile(fsys fs.FS, path string) (*Bundle, error) {
	data, err := fs.ReadFile(fsys, path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	b, err := Decode(data)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return b, nil
}
