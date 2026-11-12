// Package overlay loads the gateway-only connector price overlay,
// connector-price-overlay/v1 (CONNECTORS-PLAN §2.3, C4.2): what a
// connector costs, keyed <app-id>/<provider-id> or platform/<platform-id>.
//
// Overlay data is private. It lives in an operator directory, filled from
// private sources; it is never in this repository (not under ee/ either,
// which is public), never in the catalog and never in a bundle or an
// emitted file. LoadDir reads an operator's directory strictly, and Check
// holds its keys to a catalog; FindDocuments finds an overlay document
// where none may be, such as the catalog tree.
package overlay

import (
	"errors"
	"fmt"
	"io/fs"
	"path"
	"regexp"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Schema is the overlay's schema identifier.
const Schema = "connector-price-overlay/v1"

// maxFileBytes bounds one overlay file.
const maxFileBytes = 1 << 20

// Entry is one overlay document.
type Entry struct {
	Key          string        // <app-id>/<provider-id> or platform/<platform-id>
	Estimate     []Estimate    // 1 to 16
	BillingMode  string        // byok or managed
	PriceCardRef *PriceCardRef // set exactly when BillingMode is managed
	Path         string        // the file it was read from
}

// Estimate is one priced unit.
type Estimate struct {
	Unit       string
	USDPerUnit float64
	Source     string
	CheckedAt  string
}

// PriceCardRef names the tool-price-card-v1 card and SKU a managed call
// settles at.
type PriceCardRef struct{ Capability, Vendor, SKU string }

// The schema's rules (catalog/connectors/schema/connector-price-overlay.v1.json).
var (
	keyPattern        = regexp.MustCompile(`^([a-z][a-z0-9-]{1,39}/[a-z][a-z0-9-]{1,23}|platform/[a-z][a-z0-9-]{1,39})$`)
	unitPattern       = regexp.MustCompile(`^[a-z][a-z0-9_]{1,31}$`)
	sourcePattern     = regexp.MustCompile(`^https://[a-z0-9.-]+(:[0-9]{1,5})?([/?][A-Za-z0-9._~:/?@!$&'()*+,;=%-]*)?$`)
	datePattern       = regexp.MustCompile(`^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$`)
	capabilityPattern = regexp.MustCompile(`^[a-z][a-z0-9_.]*$`)
	vendorPattern     = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,47}$`)
)

const (
	maxEstimates = 16
	maxSource    = 2048
	maxSKU       = 100
)

// LoadDir reads every overlay file in an operator's directory: each
// .yaml, .yml or .json file is one connector-price-overlay/v1 document,
// refused unless it is exactly that (no unknown or missing member, no null,
// every value within its rule), and no key may appear twice. Any other
// file is refused, so nothing in the directory is silently ignored; hidden
// files (a leading dot) are skipped. No message quotes a price.
func LoadDir(fsys fs.FS) (map[string]*Entry, error) {
	entries := map[string]*Entry{}
	var errs []error
	err := fs.WalkDir(fsys, ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			errs = append(errs, err)
			return nil
		}
		if strings.HasPrefix(d.Name(), ".") && p != "." {
			if d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		if d.IsDir() {
			return nil
		}
		if !isDocument(p) {
			errs = append(errs, fmt.Errorf("%s: is not an overlay file (.yaml, .yml or .json)", p))
			return nil
		}
		e, err := readEntry(fsys, p)
		if err != nil {
			errs = append(errs, err)
			return nil
		}
		if prev, dup := entries[e.Key]; dup {
			errs = append(errs, fmt.Errorf("%s: key %s is also in %s", p, e.Key, prev.Path))
			return nil
		}
		entries[e.Key] = e
		return nil
	})
	if err != nil {
		errs = append(errs, err)
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	return entries, nil
}

func isDocument(p string) bool {
	switch path.Ext(p) {
	case ".yaml", ".yml", ".json":
		return true
	}
	return false
}

func readEntry(fsys fs.FS, p string) (*Entry, error) {
	info, err := fs.Stat(fsys, p)
	if err != nil {
		return nil, err
	}
	if info.Size() > maxFileBytes {
		return nil, fmt.Errorf("%s: is larger than %d bytes", p, maxFileBytes)
	}
	data, err := fs.ReadFile(fsys, p)
	if err != nil {
		return nil, err
	}
	var v any
	if path.Ext(p) == ".json" {
		v, err = canonjson.Parse(data)
	} else {
		v, err = canonjson.ParseYAML(data)
	}
	if err != nil {
		return nil, fmt.Errorf("%s: %w", p, err)
	}
	e, errs := decodeEntry(v)
	if len(errs) > 0 {
		return nil, fmt.Errorf("%s: %w", p, errors.Join(errs...))
	}
	e.Path = p
	return e, nil
}

// Check refuses an entry whose key names no provider or platform of cat.
func Check(entries map[string]*Entry, cat *manifest.Catalog) error {
	var errs []error
	for key, e := range entries {
		first, second, _ := strings.Cut(key, "/")
		switch {
		case first == "platform" && cat.Platform(second) != nil:
		case first != "platform" && cat.App(first) != nil && cat.App(first).Provider(second) != nil:
		default:
			errs = append(errs, fmt.Errorf("%s: key %s names no provider or platform in the catalog", e.Path, key))
		}
	}
	return errors.Join(errs...)
}

// checkedDate holds a date to its pattern and the calendar.
func checkedDate(s string) bool {
	if !datePattern.MatchString(s) {
		return false
	}
	_, err := time.Parse("2006-01-02", s)
	return err == nil
}
