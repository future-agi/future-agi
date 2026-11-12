package auth

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/httppath"
)

const EnvelopeHeader = "X-FA-Envelope"
const GrantHeader = "X-FA-Grant"
const EnvelopeSkew = 5 * time.Second
const EnvelopeKid = "k1"

var ErrEnvelopeRejected = errors.New("service_envelope_rejected")

// EnvelopeClaims is the 19-field fe1 canonical form. It never contains a MAC,
// a pair secret, or the accompanying opaque grant.
type EnvelopeClaims struct {
	Mode, Kid, Iss, Aud, Method, Route                           string
	Iat, Exp                                                     int64
	ID, Kind, Org, Workspace, User, Via, Sub, Scope, Body, Grant string
}

func (c EnvelopeClaims) fields() []string {
	return []string{"fe1", c.Mode, c.Kid, c.Iss, c.Aud, c.Method, c.Route, strconv.FormatInt(c.Iat, 10), strconv.FormatInt(c.Exp, 10), c.ID, c.Kind, c.Org, c.Workspace, c.User, c.Via, c.Sub, c.Scope, c.Body, c.Grant}
}

var uuidRE = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var nonceRE = regexp.MustCompile(`^[0-9a-f]{32}$`)
var digestRE = regexp.MustCompile(`^[0-9a-f]{64}$`)
var kidRE = regexp.MustCompile(`^k[0-9]{1,3}$`)
var integerRE = regexp.MustCompile(`^(0|[1-9][0-9]{0,10})$`)
var keyViaRE = regexp.MustCompile(`^key:[A-Za-z0-9_-]{1,64}$`)
var routeRE = regexp.MustCompile(`^/[A-Za-z0-9/_~.:-]*$`)

// One delegated batch's routes, for the ids batch.generateBatchID makes:
// "batch-" and 12 lowercase hex digits (auth cannot import batch).
var batchItemRouteRE = regexp.MustCompile(`^/-/batches/batch-[0-9a-f]{12}$`)
var batchCancelRouteRE = regexp.MustCompile(`^/-/batches/batch-[0-9a-f]{12}/cancel$`)

// delegatedBatchRoute: FA's delegated batch routes, all under the batch scope:
// submit and list (/-/batches), get one batch, and cancel it.
func delegatedBatchRoute(route string) bool {
	return route == "/-/batches" || batchItemRouteRE.MatchString(route) || batchCancelRouteRE.MatchString(route)
}

func EnvelopeDigest(body []byte) string {
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}
func NewEnvelopeID() (string, error) {
	var id [16]byte
	_, err := rand.Read(id[:])
	return hex.EncodeToString(id[:]), err
}
func envelopeMAC(secret string, c EnvelopeClaims, canonical []byte) []byte {
	// HKDF-SHA256, one expansion block (32 bytes).
	extract := hmac.New(sha256.New, []byte("fe1"))
	extract.Write([]byte(secret))
	expand := hmac.New(sha256.New, extract.Sum(nil))
	expand.Write(append([]byte(c.Iss+">"+c.Aud+"|"+c.Kid), 1))
	mac := hmac.New(sha256.New, expand.Sum(nil))
	mac.Write(canonical)
	return mac.Sum(nil)
}

// SignEnvelope signs request mode only. The gateway cannot issue a grant.
// Callers must build a new ID and timestamps for every attempt.
func SignEnvelope(secret string, c EnvelopeClaims) (string, error) {
	if len(secret) < 32 || c.Mode != "request" || !validClaims(c) {
		return "", ErrEnvelopeRejected
	}
	canonical := []byte(strings.Join(c.fields(), "\n"))
	token := "fe1." + base64.RawURLEncoding.EncodeToString(canonical) + "." + base64.RawURLEncoding.EncodeToString(envelopeMAC(secret, c, canonical))
	if len(token) > 2048 {
		return "", ErrEnvelopeRejected
	}
	return token, nil
}

// parseEnvelope enforces canonical base64url and syntax even for the opaque
// grant. Reading its claims is NOT verifying its MAC; FA alone can do that.
func parseEnvelope(token string) (EnvelopeClaims, []byte, []byte, error) {
	reject := func() (EnvelopeClaims, []byte, []byte, error) { return EnvelopeClaims{}, nil, nil, ErrEnvelopeRejected }
	if len(token) > 2048 {
		return reject()
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 || parts[0] != "fe1" {
		return reject()
	}
	raw, err := base64.RawURLEncoding.Strict().DecodeString(parts[1])
	if err != nil || base64.RawURLEncoding.EncodeToString(raw) != parts[1] {
		return reject()
	}
	mac, err := base64.RawURLEncoding.Strict().DecodeString(parts[2])
	if err != nil || len(mac) != sha256.Size || base64.RawURLEncoding.EncodeToString(mac) != parts[2] {
		return reject()
	}
	f := strings.Split(string(raw), "\n")
	if len(f) != 19 || f[0] != "fe1" || !integerRE.MatchString(f[7]) || !integerRE.MatchString(f[8]) {
		return reject()
	}
	iat, _ := strconv.ParseInt(f[7], 10, 64)
	exp, _ := strconv.ParseInt(f[8], 10, 64)
	c := EnvelopeClaims{f[1], f[2], f[3], f[4], f[5], f[6], iat, exp, f[9], f[10], f[11], f[12], f[13], f[14], f[15], f[16], f[17], f[18]}
	if !validClaims(c) {
		return reject()
	}
	return c, raw, mac, nil
}
func validClaims(c EnvelopeClaims) bool {
	if !kidRE.MatchString(c.Kid) || !nonceRE.MatchString(c.ID) || !uuidRE.MatchString(c.Org) || (c.Workspace != "-" && !uuidRE.MatchString(c.Workspace)) || (c.User != "-" && !uuidRE.MatchString(c.User)) || c.Iat < 0 || c.Exp <= c.Iat || c.Exp > 99999999999 {
		return false
	}
	if c.Mode == "grant" {
		return validGrant(c)
	}
	if c.Mode != "request" || c.Kind != "-" || c.Sub != "-" || c.Exp-c.Iat != 60 || !digestRE.MatchString(c.Body) || !requestMethodAllowed(c) || !routeRE.MatchString(c.Route) || httppath.Canonical(c.Route) != strings.TrimSuffix(c.Route, "/") {
		return false
	}
	switch c.Aud {
	case "gw-delegated":
		if c.Iss != "fa" || (c.Route != "/-/delegated/v1/chat/completions" && !delegatedBatchRoute(c.Route)) {
			return false
		}
		if !strings.HasPrefix(c.Via, "grant:") || c.User == "-" {
			return false
		}
		if delegatedBatchRoute(c.Route) {
			if c.Scope != "batch" {
				return false
			}
		} else if c.Scope != "playground" && c.Scope != "tryit" && c.Scope != "guardrail-test" {
			return false
		}
	case "fa-eval":
		if c.Iss != "gateway" || c.Route != "/sdk/api/v1/eval/" || c.Scope != "eval" || c.User == "-" {
			return false
		}
	case "fa-relay":
		return c.Iss == "fa" && c.Route == "/call-websocket/" && c.Scope == "relay" && c.Via == "svc" && c.User == "-" && c.Grant == "-"
	default:
		return false
	}
	if strings.HasPrefix(c.Via, "grant:") {
		return nonceRE.MatchString(strings.TrimPrefix(c.Via, "grant:")) && digestRE.MatchString(c.Grant)
	}
	return keyViaRE.MatchString(c.Via) && c.Grant == "-"
}

// requestMethodAllowed: every request envelope is a POST except FA's
// delegated batch reads, GET /-/batches (733 add.13(3)) and GET
// /-/batches/{id} (733 add.16(a)). One batch's routes take only their own
// method: GET reads it, POST /-/batches/{id}/cancel cancels it. Each still
// binds the route, the body digest, the batch scope and the forwarded grant.
func requestMethodAllowed(c EnvelopeClaims) bool {
	if c.Aud == "gw-delegated" {
		switch {
		case batchItemRouteRE.MatchString(c.Route):
			return c.Method == "GET"
		case batchCancelRouteRE.MatchString(c.Route):
			return c.Method == "POST"
		case c.Route == "/-/batches":
			return c.Method == "GET" || c.Method == "POST"
		}
	}
	return c.Method == "POST"
}
func validGrant(c EnvelopeClaims) bool {
	if c.Iss != "fa" || c.Method != "-" || c.Route != "-" || c.Body != "-" || c.Grant != "-" {
		return false
	}
	max := int64(0)
	switch c.Kind {
	case "playground", "tryit", "guardrail-test", "batch":
		prefix := "chat:"
		max = 900
		if c.Kind == "batch" {
			prefix = "batch:"
			max = 86400
		}
		if c.Aud != "fa-eval-grant" || c.Scope != "eval" || !strings.HasPrefix(c.Sub, prefix) || !uuidRE.MatchString(strings.TrimPrefix(c.Sub, prefix)) {
			return false
		}
	case "sandbox":
		parts := strings.Split(c.Sub, ":")
		max = 86400
		if c.Aud != "collector-ingest" || c.Scope != "traces" || len(parts) != 3 || parts[0] != "attempt" || !uuidRE.MatchString(parts[1]) || !uuidRE.MatchString(parts[2]) {
			return false
		}
	case "platform-emit":
		return c.Aud == "collector-emit" && c.Scope == "traces" && (c.Sub == "emit:provider" || c.Sub == "emit:voice" || c.Sub == "emit:sim") && c.Via == "svc" && c.User == "-" && c.Exp-c.Iat <= 60
	default:
		return false // EE grants are OFF at launch (733 addendum 4).
	}
	return c.Via == "person" && c.User != "-" && c.Exp-c.Iat <= max
}

// EnvelopeVerifier is single-replica only. Epoch retains subsecond precision:
// rounding down permits a replay after two starts in the same Unix second.
type EnvelopeVerifier struct {
	secret   string
	epoch    time.Time
	mu       sync.Mutex
	used     map[string]int64
	lastGC   int64
	rejected func(string)
}

func NewEnvelopeVerifier(secret string, epoch time.Time, observe ...func(string)) *EnvelopeVerifier {
	v := &EnvelopeVerifier{secret: secret, epoch: epoch, used: make(map[string]int64)}
	if len(observe) > 0 {
		v.rejected = observe[0]
	}
	return v
}
func (v *EnvelopeVerifier) Ready(now time.Time) bool {
	return v != nil && !now.Before(v.epoch.Add(EnvelopeSkew))
}

// VerifyDelegated binds the actual bytes, forwarded grant, route and principal
// before atomically consuming a nonce. All failures deliberately look alike.
func (v *EnvelopeVerifier) VerifyDelegated(token, grant, method, route string, body []byte, now time.Time) (EnvelopeClaims, EnvelopeClaims, error) {
	reject := func(reason string) (EnvelopeClaims, EnvelopeClaims, error) {
		if v != nil && v.rejected != nil {
			v.rejected(reason)
		}
		return EnvelopeClaims{}, EnvelopeClaims{}, ErrEnvelopeRejected
	}
	if v == nil || len(v.secret) < 32 {
		return reject("secret_unavailable")
	}
	c, raw, mac, err := parseEnvelope(token)
	if err != nil || c.Kid != EnvelopeKid || c.Mode != "request" || c.Iss != "fa" || c.Aud != "gw-delegated" || c.Method != method || c.Route != route || c.Body != EnvelopeDigest(body) || c.Grant != EnvelopeDigest([]byte(grant)) || now.Unix() < c.Iat-5 || now.Unix() > c.Exp || time.Unix(c.Iat, 0).Before(v.epoch.Add(EnvelopeSkew)) || !hmac.Equal(mac, envelopeMAC(v.secret, c, raw)) {
		return reject("request_binding")
	}
	g, err := boundGrant(c, grant, now)
	if err != nil || c.Scope != g.Kind {
		return reject("grant_binding")
	}
	v.mu.Lock()
	defer v.mu.Unlock()
	if now.Unix() > v.lastGC {
		for id, exp := range v.used {
			if exp < now.Unix() {
				delete(v.used, id)
			}
		}
		v.lastGC = now.Unix()
	}
	if _, ok := v.used[c.ID]; ok {
		return reject("replay")
	}
	// Bound memory under overload; never evict an unexpired nonce.
	if len(v.used) >= 100000 {
		return reject("nonce_capacity")
	}
	v.used[c.ID] = c.Exp
	return c, g, nil
}

// boundGrant authenticates no grant: it compares its opaque claims and digest
// with a separately MAC-authenticated request. FA still owns grant validation.
func boundGrant(c EnvelopeClaims, token string, now time.Time) (EnvelopeClaims, error) {
	g, _, _, err := parseEnvelope(token)
	if err != nil || g.Mode != "grant" || g.Kid != EnvelopeKid || g.Aud != "fa-eval-grant" || c.Grant != EnvelopeDigest([]byte(token)) || c.Via != "grant:"+g.ID || c.Org != g.Org || c.User != g.User || c.Workspace != g.Workspace || now.Unix() < g.Iat-5 || now.Unix() > g.Exp {
		return EnvelopeClaims{}, ErrEnvelopeRejected
	}
	return g, nil
}

func (c EnvelopeClaims) String() string { return fmt.Sprintf("fe1 %s %s>%s", c.Mode, c.Iss, c.Aud) }
