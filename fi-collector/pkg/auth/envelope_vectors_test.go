package auth

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
)

// Exact upstream library/vector commit; copied byte-for-byte, never generated
// by the implementation under test.
const envelopeLibraryPin = "7c8df22223e45267833220deac22fc02bdabae01"
const envelopeVectorsDigest = "sha256:67cc19a792c94e60842a42e8c9f2939963f4b8c132f66e9277e4814be23b8931"

type wireVector struct {
	Name     string            `json:"name"`
	Claims   map[string]string `json:"claims"`
	Token    string            `json:"token"`
	PublicID string            `json:"public_id"`
	Now      int64             `json:"now"`
}
type rejectionVector struct {
	Name     string `json:"name"`
	Source   string `json:"source"`
	Token    string `json:"token"`
	PublicID string `json:"public_id"`
	Now      int64  `json:"now"`
}
type registryVector struct {
	Name     string            `json:"name"`
	Claims   map[string]string `json:"claims"`
	Row      map[string]any    `json:"row"`
	Liveness grantLiveness     `json:"liveness"`
	Expected bool              `json:"expected"`
}

func TestSharedCollectorEnvelopeVectors(t *testing.T) {
	raw, err := os.ReadFile("../../../api_contracts/service_envelope/v1/vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	if "sha256:"+hex.EncodeToString(sum[:]) != envelopeVectorsDigest {
		t.Fatalf("vectors differ from pinned library %s", envelopeLibraryPin)
	}
	var corpus struct {
		Schema     string            `json:"schema"`
		Fields     []string          `json:"fields"`
		Secret     string            `json:"test_only_pair_secret"`
		Valid      []wireVector      `json:"valid_macs"`
		Rejections []rejectionVector `json:"rejections"`
		Registry   []registryVector  `json:"registry_cases"`
	}
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	if corpus.Schema != "fe1-v1" || len(corpus.Fields) != 19 || len(corpus.Valid) != 14 || len(corpus.Rejections) != 77 || len(corpus.Registry) != 70 {
		t.Fatal("unexpected vector inventory")
	}
	// ICU09: exact identity sets as well as exact bytes. This accepted L1b
	// corpus retains every L2 vector unchanged and adds batch boundaries.
	var validNames, rejectionNames, registryNames []string
	for _, v := range corpus.Valid {
		validNames = append(validNames, v.Name)
	}
	for _, v := range corpus.Rejections {
		rejectionNames = append(rejectionNames, v.Name)
	}
	for _, v := range corpus.Registry {
		registryNames = append(registryNames, v.Name)
	}
	for _, inventory := range []struct {
		names  []string
		digest string
	}{
		{validNames, "ecd7d00194ce3d1170cbd82b86da050f98269b1a7693458df2e7a0b9c24be2fd"},
		{rejectionNames, "b3145256f63449ec23125fcef6aa891ab9ad837360d5726d08aaa79d3d84aafe"},
		{registryNames, "296f8237d1b60c570808a22c8aca99039a4c982ad22896a2bba5989e646660af"},
	} {
		sort.Strings(inventory.names)
		for i := 1; i < len(inventory.names); i++ {
			if inventory.names[i] == inventory.names[i-1] {
				t.Fatal("duplicate vector identity")
			}
		}
		sum := sha256.Sum256([]byte(strings.Join(inventory.names, "\n") + "\n"))
		if hex.EncodeToString(sum[:]) != inventory.digest {
			t.Fatal("vector identity set changed")
		}
	}
	p := mustPeppers(t, corpus.Secret)
	sources := map[string]wireVector{}
	for _, v := range corpus.Valid {
		sources[v.Name] = v
		t.Run("valid/"+v.Name, func(t *testing.T) {
			c, err := VerifyCollectorGrant(v.PublicID, v.Token, p, []string{"k1"}, time.Unix(v.Now, 0))
			collector := v.Claims["kind"] == "sandbox" || v.Claims["kind"] == "platform-emit"
			if collector {
				if err != nil || c == nil {
					t.Fatalf("valid collector grant refused: %v", err)
				}
			} else if err == nil {
				t.Fatal("another service's envelope admitted")
			}
		})
	}
	for _, v := range corpus.Rejections {
		t.Run("reject/"+v.Name, func(t *testing.T) {
			source, ok := sources[v.Source]
			if !ok {
				t.Fatal("unknown vector source")
			}
			token, api, now := source.Token, source.PublicID, source.Now
			if v.Token != "" {
				token = v.Token
			}
			if v.PublicID != "" {
				api = v.PublicID
			}
			if v.Now != 0 {
				now = v.Now
			}
			if _, err := VerifyCollectorGrant(api, token, p, []string{"k1"}, time.Unix(now, 0)); err == nil {
				t.Fatal("rejection vector admitted")
			}
		})
	}
	count := 0
	for _, v := range corpus.Registry {
		if v.Claims["kind"] != "sandbox" && v.Claims["kind"] != "platform-emit" {
			continue
		}
		count++
		t.Run("registry/"+v.Name, func(t *testing.T) {
			fields := make([]string, 0, 19)
			for _, key := range corpus.Fields {
				fields = append(fields, v.Claims[key])
			}
			now, err := strconv.ParseInt(v.Claims["iat"], 10, 64)
			if err != nil {
				t.Fatal(err)
			}
			c, err := VerifyCollectorGrant("fit_"+v.Claims["id"], signedGrantFields(fields, corpus.Secret), p, []string{"k1"}, time.Unix(now, 0))
			if err != nil {
				t.Fatal("invalid registry vector claims")
			}
			accepted := v.Liveness.allows(c)
			if c.Kind == "sandbox" {
				accepted = accepted && vectorRegistryMatches(v.Row, c)
			}
			if accepted != v.Expected {
				t.Fatalf("registry accepted=%v expected=%v", accepted, v.Expected)
			}
		})
	}
	if count != 31 {
		t.Fatalf("collector registry inventory=%d", count)
	}
}

func vectorRegistryMatches(row map[string]any, c *GrantClaims) bool {
	if row == nil {
		return false
	}
	// The shared language-neutral rows encode integer epochs as strings; PG
	// returns JSON numbers. Invalid integer fixtures are refused by the adapter.
	normalized := make(map[string]any, len(row))
	for k, v := range row {
		normalized[k] = v
	}
	for _, key := range []string{"iat", "exp"} {
		text, ok := normalized[key].(string)
		if !ok {
			return false
		}
		value, err := strconv.ParseInt(text, 10, 64)
		if err != nil {
			return false
		}
		normalized[key] = value
	}
	raw, err := json.Marshal(normalized)
	if err != nil {
		return false
	}
	var record grantRecord
	return json.Unmarshal(raw, &record) == nil && record.matches(c)
}
