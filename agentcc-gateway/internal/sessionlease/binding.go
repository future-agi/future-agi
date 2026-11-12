package sessionlease

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"strconv"
	"strings"
)

type routing struct {
	org, workspace, session, id, directory string
	keys                                   []string
}

func digest(fields ...string) string {
	h := sha256.New()
	var size [8]byte
	for _, f := range fields {
		binary.BigEndian.PutUint64(size[:], uint64(len(f)))
		_, _ = h.Write(size[:])
		_, _ = h.Write([]byte(f))
	}
	return hex.EncodeToString(h.Sum(nil))
}
func integer(n int64) string { return strconv.FormatInt(n, 10) }
func bindingFields(b Binding) []string {
	return []string{"binding-v1", b.ID, b.Scope.OrgID, b.Scope.WorkspaceID, b.Actor.Kind, b.Actor.PersonID, b.Actor.CredentialID, b.SessionHash, integer(int64(b.Purpose))}
}
func policyDigest(p Policy) string {
	fields := []string{"policy-v1", p.Version, integer(int64(p.MaxIdle)), integer(int64(p.MaxAbsolute))}
	for _, l := range []Limits{p.Sessions, p.Calls, p.Streams} {
		fields = append(fields, integer(int64(l.Org)), integer(int64(l.Workspace)), integer(int64(l.Session)))
	}
	return digest(fields...)
}
func routeBinding(b Binding) routing {
	r := routing{org: digest("org-v1", b.Scope.OrgID), workspace: digest("workspace-v1", b.Scope.OrgID, b.Scope.WorkspaceID), session: digest("session-v1", b.Scope.OrgID, b.Scope.WorkspaceID, b.SessionHash), id: digest("id-v1", b.ID)}
	prefix := "mcpl4:{o:" + r.org + "}:"
	budget := prefix + "budget:" + integer(int64(b.Purpose)) + ":"
	r.keys = []string{prefix + "record:" + r.id, prefix + "live:" + r.id, budget + "org", budget + "workspace:" + r.workspace, budget + "session:" + r.session, prefix + "policy:" + integer(int64(b.Purpose)), prefix + "guard:" + integer(int64(b.Purpose))}
	return r
}
func (r routing) directoryKey() string { return "mcpl4:dir:{i:" + r.id + "}" }
func routeFor(req Request, p Policy) routing {
	r := routeBinding(req.Binding)
	fields := append(bindingFields(req.Binding), integer(int64(req.TTL)), integer(int64(req.Idle)), integer(int64(req.Absolute)), integer(int64(req.Limits.Org)), integer(int64(req.Limits.Workspace)), integer(int64(req.Limits.Session)))
	r.directory = strings.Join([]string{r.org, r.workspace, r.session, r.id, integer(int64(req.Purpose)), digest(bindingFields(req.Binding)...), digest(fields...), policyDigest(p)}, "|")
	return r
}
func bindDirectory(raw string, b Binding, p Policy) (routing, error) {
	r := routeBinding(b)
	parts := strings.Split(raw, "|")
	if len(parts) != 8 {
		return r, ErrCorrupt
	}
	expected := []string{r.org, r.workspace, r.session, r.id, integer(int64(b.Purpose)), digest(bindingFields(b)...)}
	for i, v := range expected {
		if parts[i] != v {
			return r, ErrBinding
		}
	}
	for _, i := range []int{0, 1, 2, 3, 5, 6, 7} {
		decoded, err := hex.DecodeString(parts[i])
		if err != nil || len(decoded) != 32 || strings.ToLower(parts[i]) != parts[i] {
			return r, ErrCorrupt
		}
	}
	if parts[7] != policyDigest(p) {
		return r, ErrBinding
	}
	r.directory = raw
	return r, nil
}
