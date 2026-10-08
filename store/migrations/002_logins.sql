-- GPR Studio logins. Passwords are stored only as Argon2id hashes (the hash embeds its own salt
-- and parameters); a session cookie's value is stored only as its SHA-256, so neither a database
-- dump nor a backup can be replayed to sign in.

CREATE TABLE users (
    user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL CHECK (username ~ '^[A-Za-z0-9._-]{2,64}$'),
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX users_username ON users (lower(username));

-- Every attempt, kept: who (as typed, so guesses at unknown names show up too), when, from where.
CREATE TABLE login_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    username TEXT NOT NULL,
    user_id BIGINT REFERENCES users (user_id),
    event TEXT NOT NULL CHECK (event IN ('login', 'login_failed', 'locked_out', 'logout')),
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX login_events_username_at ON login_events (lower(username), at);
CREATE INDEX login_events_ip_at ON login_events (ip, at);

CREATE TABLE sessions (
    token_sha256 BYTEA PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX sessions_user ON sessions (user_id);
