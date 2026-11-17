package main

import (
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
)

func TestStaticClientCLIRequiresRegisteredMetadataBeforeServing(t *testing.T) {
	t.Setenv(oauthClientSecretEnv, "cn24-test-secret-not-real")
	base := []string{"--addr", "127.0.0.1:0", "--auth", "oauth", "--oauth-client", "fixture-client"}
	metadata := []string{"--oauth-redirect-uri", "http://127.0.0.1:9/callback", "--oauth-auth-method", "client_secret_post",
		"--oauth-grant", "authorization_code", "--oauth-grant", "refresh_token"}
	o, err := parseFlags(append(append([]string{}, base...), metadata...))
	if err != nil {
		t.Fatal(err)
	}
	c := o.registeredClients()["fixture-client"]
	if c.Secret != "cn24-test-secret-not-real" || c.AuthMethod != "client_secret_post" ||
		!reflect.DeepEqual(c.RedirectURIs, []string{"http://127.0.0.1:9/callback"}) ||
		!reflect.DeepEqual(c.GrantTypes, []string{"authorization_code", "refresh_token"}) ||
		!reflect.DeepEqual(c.ResponseTypes, []string{"code"}) {
		t.Fatal("CLI did not retain explicit registration metadata")
	}
	if _, err := mcpmock.New(mcpmock.Config{Auth: "oauth", OAuthClients: o.registeredClients()}, &mcpmock.Fixtures{}); err != nil {
		t.Fatal(err)
	}
	for _, omitted := range []string{"--oauth-redirect-uri", "--oauth-auth-method", "--oauth-grant"} {
		args := append([]string{}, base...)
		for i := 0; i < len(metadata); i += 2 {
			if metadata[i] != omitted {
				args = append(args, metadata[i:i+2]...)
			}
		}
		o, err := parseFlags(args)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := mcpmock.New(mcpmock.Config{Auth: "oauth", OAuthClients: o.registeredClients()}, &mcpmock.Fixtures{}); err == nil {
			t.Errorf("client missing %s could start serving", omitted)
		}
	}
}
