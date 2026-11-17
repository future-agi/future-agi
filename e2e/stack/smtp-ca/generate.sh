#!/usr/bin/env bash
# e2e only: a throwaway test CA and the mail sink's certificate, made per stack at `bin/e2e up` and never committed.
# Since 57b89c77 the product verifies the SMTP server's STARTTLS certificate (agentcc/services/email_sender.py:
# starttls(context=ssl.create_default_context())). mailpit serves the leaf below for smtp.e2e-public.test, and the
# app-side containers trust the CA through SSL_CERT_DIR, which adds a directory to OpenSSL's default trust and keeps
# the image's own roots (the default CA file stays loaded). Usage: generate.sh <dir>. Reuses a complete, unexpired set.
set -euo pipefail
dir=${1:?usage: generate.sh <dir>}
[[ "$dir" == /* && ! -L "$dir" ]] || { echo "smtp-ca: <dir> must be an absolute, non-symlink path" >&2; exit 1; }
mkdir -p "$dir/trust"
if [[ -s "$dir/ca.pem" && -s "$dir/mailpit.pem" && -s "$dir/mailpit.key" ]] && ls "$dir"/trust/*.0 > /dev/null 2>&1 \
   && openssl x509 -checkend 86400 -noout -in "$dir/mailpit.pem" > /dev/null 2>&1; then
  exit 0
fi
umask 077
tmp=$(mktemp -d "$dir/.gen.XXXXXX"); trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/ca.cnf" <<'CNF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = futureagi e2e SMTP test CA
[v3_ca]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
[v3_leaf]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:smtp.e2e-public.test, DNS:smtp-sink, DNS:localhost, IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
CNF
openssl req -x509 -new -newkey rsa:2048 -nodes -days 30 -config "$tmp/ca.cnf" -extensions v3_ca \
  -keyout "$tmp/ca.key" -out "$tmp/ca.pem" 2> /dev/null
openssl req -new -newkey rsa:2048 -nodes -subj "/CN=smtp.e2e-public.test" \
  -keyout "$tmp/mailpit.key" -out "$tmp/mailpit.csr" 2> /dev/null
openssl x509 -req -in "$tmp/mailpit.csr" -CA "$tmp/ca.pem" -CAkey "$tmp/ca.key" -set_serial "0x$(openssl rand -hex 8)" \
  -days 30 -extfile "$tmp/ca.cnf" -extensions v3_leaf -out "$tmp/mailpit.pem" 2> /dev/null
openssl verify -CAfile "$tmp/ca.pem" "$tmp/mailpit.pem" > /dev/null
rm -f "$dir"/trust/*.0
cp "$tmp/ca.pem" "$dir/trust/$(openssl x509 -hash -noout -in "$tmp/ca.pem").0"
# Readable by the containers' own users (mailpit and the app do not run as this host user); the CA key is not kept.
chmod 0644 "$tmp/ca.pem" "$tmp/mailpit.pem" "$tmp/mailpit.key" "$dir"/trust/*.0; chmod 0755 "$dir" "$dir/trust"
mv -f "$tmp/ca.pem" "$dir/ca.pem"; mv -f "$tmp/mailpit.pem" "$dir/mailpit.pem"; mv -f "$tmp/mailpit.key" "$dir/mailpit.key"
