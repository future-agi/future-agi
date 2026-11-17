package connectors

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// RuntimeRecord preserves the exact provider bytes admitted from this build's
// embedded bundle. Raw is absent for directory or caller-constructed catalogs.
// A returned record is a defensive copy, not an admission constructor.
type RuntimeRecord struct {
	AppID, ProviderID, Kind string
	Retired                 bool
	Raw                     json.RawMessage
}

type runtimeSnapshot struct {
	records []RuntimeRecord
	digest  string
}

// RuntimeSnapshot is the sole production observation seam. Only Embedded can
// seal bytes here, AFTER bundle.Decode and Bundle.Catalog (schema, L0/L1 and
// checkEnablement on declaration-aware builds). No source label, caller bool,
// directory file, typed projection or separate evidence store can supply Raw.
func (c *Catalog) RuntimeSnapshot() ([]RuntimeRecord, string) {
	if c == nil {
		return nil, ""
	}
	if s := c.runtimeSnapshot; s != nil {
		out := make([]RuntimeRecord, len(s.records))
		for i, r := range s.records {
			out[i] = r
			out[i].Raw = append(json.RawMessage(nil), r.Raw...)
		}
		return out, s.digest
	}
	// Preserve named unknown-revision refusals for directory/legacy callers,
	// but never hash their typed projection into alleged admission evidence.
	return runtimeIdentities(c.Records), ""
}

func runtimeIdentities(cat *manifest.Catalog) []RuntimeRecord {
	var out []RuntimeRecord
	if cat == nil {
		return nil
	}
	for _, a := range cat.Apps {
		if a == nil {
			continue
		}
		for _, p := range a.Providers {
			if p == nil {
				continue
			}
			out = append(out, RuntimeRecord{AppID: a.ID, ProviderID: p.ID, Kind: p.Kind,
				Retired: a.Lifecycle.Status == "sunset" || p.Lifecycle.Status == "sunset"})
		}
	}
	return out
}

// Private: its production caller has already validated THESE SAME bytes, not
// a reread or reserialized manifest. This does not replace the admission rules.
func captureRuntimeSnapshot(cat *manifest.Catalog, data []byte) (*runtimeSnapshot, error) {
	refuse := errors.New("embedded connector catalog: runtime snapshot mismatch")
	var raw struct {
		Providers []json.RawMessage `json:"providers"`
	}
	if json.Unmarshal(data, &raw) != nil {
		return nil, refuse
	}
	records := runtimeIdentities(cat)
	if len(records) == 0 || len(records) != len(raw.Providers) {
		return nil, refuse
	}
	byID := map[string]RuntimeRecord{}
	for _, rec := range raw.Providers {
		var key struct {
			App  string `json:"app"`
			ID   string `json:"id"`
			Kind string `json:"kind"`
		}
		if json.Unmarshal(rec, &key) != nil {
			return nil, refuse
		}
		id := key.App + "/" + key.ID
		if _, dup := byID[id]; dup {
			return nil, refuse
		}
		byID[id] = RuntimeRecord{AppID: key.App, ProviderID: key.ID, Kind: key.Kind, Raw: append(json.RawMessage(nil), rec...)}
	}
	for i, rec := range records {
		id := rec.AppID + "/" + rec.ProviderID
		source, ok := byID[id]
		if !ok || source.Kind != rec.Kind {
			return nil, refuse
		}
		records[i].Raw = source.Raw
		delete(byID, id)
	}
	if len(byID) != 0 {
		return nil, refuse
	}
	digest := sha256.Sum256(data)
	return &runtimeSnapshot{records: records, digest: hex.EncodeToString(digest[:])}, nil
}
