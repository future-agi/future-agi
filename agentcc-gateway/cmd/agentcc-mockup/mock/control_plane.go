package mock

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
)

// The mock stands in for the app (Django) as a scenario gateway's control
// plane. Since 733 a control-plane gateway refuses an org-bound config.yaml
// seed: its config keys are deployment identities only (internal, no org_id,
// no user_id), and an org's key is a person's, listed by the app. So a
// scenario's org keys enter through the key list the gateway syncs, which
// the mock answers in the app's 733 shape from the keys the scenario
// registers:
//
//	GET    /agentcc/api-keys/bulk/      {status, result, snapshot, retired,
//	                                    config_conflicts}: the list a
//	                                    control-plane gateway syncs
//	POST   /__control-plane/keys        {"keys": [{id, key | key_hash, name?,
//	                                    owner?, models?, providers?,
//	                                    metadata}]}: register (or replace, by
//	                                    id) person-owned keys; metadata needs
//	                                    org_id and user_id, as the app lists
//	                                    no other
//	DELETE /__control-plane/keys/{id}   retire one: the list leaves it out and
//	                                    names it in retired
//	GET    /__control-plane/keys        what is registered and retired, and
//	                                    how many lists the gateway fetched
//
// The list is authoritative (snapshot.complete, issued_ever the number of
// keys ever registered), so a gateway drops a key it made that is not
// registered once the sync grace has passed: register every key a scenario
// mints, as the app stores every key it creates. config_conflicts names each
// hash in X-Agentcc-Config-Key-Hashes that a registered or retired key holds,
// and is always present (an empty list when none), as the 733 app answers:
// without it a 733 gateway keeps its deployment seeds pending.
//
// The mock does not serve /agentcc/org-configs/bulk/: any answer would
// replace the org configs a scenario pushes (PUT /-/orgs/{id}/config), so the
// gateway's org sync fails and keeps them. Neither bulk path is an upstream
// call, so /__received leaves both out (isInspectionPath). POST /__reset
// keeps the registered keys: they stand for the app's database, not for what
// the mock was sent.
const (
	keyListPath       = "/agentcc/api-keys/bulk/"
	orgConfigListPath = "/agentcc/org-configs/bulk/"
	// configKeyHashesHeader carries a gateway's config seed hashes.
	configKeyHashesHeader = "X-Agentcc-Config-Key-Hashes"
	maxConfigKeyHashes    = 32
)

// cpKey is a key the app lists: its hash, never the raw key.
type cpKey struct {
	ID        string            `json:"id"`
	Name      string            `json:"name,omitempty"`
	Owner     string            `json:"owner,omitempty"`
	KeyHash   string            `json:"key_hash"`
	Models    []string          `json:"models,omitempty"`
	Providers []string          `json:"providers,omitempty"`
	Metadata  map[string]string `json:"metadata"`
}

// cpRetired is a retired key as the list names it.
type cpRetired struct {
	ID      string `json:"id"`
	KeyHash string `json:"key_hash"`
}

// controlPlane holds the keys registered with the mock's control plane.
type controlPlane struct {
	mu      sync.Mutex
	active  map[string]cpKey // by id
	retired map[string]cpRetired
	order   []string // ids in first-registration order, for a stable list
	fetches int
}

func newControlPlane() *controlPlane {
	return &controlPlane{active: map[string]cpKey{}, retired: map[string]cpRetired{}}
}

func (s *Server) controlPlaneRoutes() {
	cp := s.cp
	s.mux.HandleFunc("GET "+keyListPath, cp.handleKeyList)
	s.mux.HandleFunc("POST /__control-plane/keys", cp.handleRegister)
	s.mux.HandleFunc("DELETE /__control-plane/keys/{id}", cp.handleRetire)
	s.mux.HandleFunc("GET /__control-plane/keys", cp.handleView)
}

// registration is one key of POST /__control-plane/keys.
type registration struct {
	cpKey
	Key string `json:"key"` // raw; hashed here and never kept
}

func (cp *controlPlane) handleRegister(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Keys []registration `json:"keys"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || len(body.Keys) == 0 {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_body", `body must be {"keys": [{"id", "key" or "key_hash", "metadata": {"org_id", "user_id"}}]}`)
		return
	}
	keys := make([]cpKey, 0, len(body.Keys))
	for _, reg := range body.Keys {
		k := reg.cpKey
		if reg.Key != "" {
			sum := sha256.Sum256([]byte(reg.Key))
			k.KeyHash = hex.EncodeToString(sum[:])
		}
		k.KeyHash = strings.ToLower(k.KeyHash)
		switch {
		case k.ID == "":
			writeOpenAIError(w, http.StatusBadRequest, "invalid_key", "every key needs an id")
			return
		case !isHexSHA256(k.KeyHash):
			writeOpenAIError(w, http.StatusBadRequest, "invalid_key", "key "+k.ID+" needs a key or a hex SHA-256 key_hash")
			return
		case strings.TrimSpace(k.Metadata["org_id"]) == "" || strings.TrimSpace(k.Metadata["user_id"]) == "":
			writeOpenAIError(w, http.StatusBadRequest, "person_required", "key "+k.ID+": the app lists only person-owned keys (metadata org_id and user_id)")
			return
		}
		keys = append(keys, k)
	}
	cp.mu.Lock()
	for _, k := range keys {
		if _, known := cp.active[k.ID]; !known {
			if _, gone := cp.retired[k.ID]; !gone {
				cp.order = append(cp.order, k.ID)
			}
		}
		delete(cp.retired, k.ID)
		cp.active[k.ID] = k
	}
	n := len(cp.active)
	cp.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{"registered": len(keys), "active": n})
}

func (cp *controlPlane) handleRetire(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	cp.mu.Lock()
	k, ok := cp.active[id]
	if ok {
		delete(cp.active, id)
		cp.retired[id] = cpRetired{ID: id, KeyHash: k.KeyHash}
	}
	cp.mu.Unlock()
	if !ok {
		writeOpenAIError(w, http.StatusNotFound, "not_found", "no active key "+id)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"retired": id})
}

func (cp *controlPlane) handleView(w http.ResponseWriter, _ *http.Request) {
	cp.mu.Lock()
	active, retired := cp.listLocked()
	fetches := cp.fetches
	cp.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{"keys": active, "retired": retired, "list_fetches": fetches})
}

// handleKeyList answers the gateway's key sync as the 733 app does
// (futureagi agentcc/views/api_key_bulk.py): the active keys, the snapshot,
// the retired keys and the config conflicts. Like the app it refuses a call
// without a bearer, and a config-hash header with more than 32 values or one
// that is not 64 hex.
func (cp *controlPlane) handleKeyList(w http.ResponseWriter, r *http.Request) {
	if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
		writeOpenAIError(w, http.StatusUnauthorized, "unauthorized", "the key list needs the gateway's admin token")
		return
	}
	var asked []string
	for _, v := range r.Header.Values(configKeyHashesHeader) {
		for _, h := range strings.Split(v, ",") {
			if h = strings.ToLower(strings.TrimSpace(h)); h != "" {
				asked = append(asked, h)
			}
		}
	}
	if len(asked) > maxConfigKeyHashes {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_config_key_hashes", "at most 32 config key hashes")
		return
	}
	for _, h := range asked {
		if !isHexSHA256(h) {
			writeOpenAIError(w, http.StatusBadRequest, "invalid_config_key_hashes", "each config key hash must be 64 hex")
			return
		}
	}
	cp.mu.Lock()
	cp.fetches++
	active, retired := cp.listLocked()
	held := make(map[string]bool, len(active)+len(retired))
	for _, k := range active {
		held[k.KeyHash] = true
	}
	for _, k := range retired {
		held[k.KeyHash] = true
	}
	issuedEver := len(cp.order)
	cp.mu.Unlock()
	conflicts := []string{}
	for _, h := range asked {
		if held[h] {
			conflicts = append(conflicts, h)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":           true,
		"result":           active,
		"snapshot":         map[string]any{"complete": true, "issued_ever": issuedEver},
		"retired":          retired,
		"config_conflicts": conflicts,
	})
}

// listLocked is the active and retired keys in registration order, each a
// non-nil list. Called only under cp.mu.
func (cp *controlPlane) listLocked() ([]cpKey, []cpRetired) {
	active, retired := []cpKey{}, []cpRetired{}
	for _, id := range cp.order {
		if k, ok := cp.active[id]; ok {
			active = append(active, k)
		} else if k, ok := cp.retired[id]; ok {
			retired = append(retired, k)
		}
	}
	return active, retired
}

func isHexSHA256(s string) bool {
	if len(s) != 64 {
		return false
	}
	_, err := hex.DecodeString(s)
	return err == nil
}
