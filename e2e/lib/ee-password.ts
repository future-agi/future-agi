import crypto from 'node:crypto';
import pg from 'pg';
import { E2E } from './env';

// Give a freshly signed-up account a password the harness knows.
//
// Signup allowlists its fields by deployment mode (accounts/views/signup.py):
// OSS accepts `password` and auto-logs the user in; EE/cloud drops it and
// returns "check your email" instead. `first_signup` still marks the account
// active and still stores a password — it just generates a random one the
// caller never sees, so there is nothing to log in with.
//
// The usual recovery, having the reset link returned in the response, is
// itself gated on is_oss(), and this stack has no mail service. So on EE the
// only way to reach a usable account is to set the hash directly. The harness
// already holds Postgres credentials for its storage-lane assertions.
//
// Django stores `pbkdf2_sha256$<iterations>$<salt>$<b64 hash>`. The iteration
// count is read back from the row Django just wrote rather than hard-coded, so
// a Django upgrade that raises the default cannot silently produce a hash the
// backend will not verify.
export async function setKnownPassword(email: string, password: string): Promise<void> {
  const client = new pg.Client({ connectionString: E2E.pgUrl });
  await client.connect();
  try {
    const { rows } = await client.query<{ password: string }>(
      'SELECT password FROM accounts_user WHERE email = $1', [email],
    );
    if (!rows.length) throw new Error(`no accounts_user row for ${email}`);

    const [algorithm, iterations] = rows[0].password.split('$');
    if (algorithm !== 'pbkdf2_sha256') {
      throw new Error(`unsupported password hasher "${algorithm}" — cannot seed an EE actor`);
    }

    const salt = crypto.randomBytes(9).toString('base64url');
    const hash = crypto
      .pbkdf2Sync(password, salt, Number(iterations), 32, 'sha256')
      .toString('base64');

    await client.query('UPDATE accounts_user SET password = $1, is_active = true WHERE email = $2',
      [`pbkdf2_sha256$${iterations}$${salt}$${hash}`, email]);
  } finally {
    await client.end();
  }
}
