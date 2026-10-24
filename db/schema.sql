-- CelLab persistence schema (PostgreSQL 16)
-- Runs automatically the FIRST time the database container starts with an empty volume.

-- Immutable: editing a spec creates a new row, never an update.
CREATE TABLE automata (
  automaton_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  spec_hash     TEXT        NOT NULL,   -- hash of the semantic fields (spec section 10.2)
  spec_version  INTEGER     NOT NULL,
  name          TEXT        NOT NULL,
  spec_text     TEXT        NOT NULL,   -- the YAML exactly as submitted
  num_states    INTEGER     NOT NULL CHECK (num_states BETWEEN 2 AND 256),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_automata_hash ON automata (spec_hash);

CREATE TABLE runs (
  run_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  automaton_id      BIGINT      NOT NULL REFERENCES automata (automaton_id),
  width             INTEGER     NOT NULL CHECK (width  > 0),
  height            INTEGER     NOT NULL CHECK (height > 0),
  checkpoint_every  BIGINT      NOT NULL CHECK (checkpoint_every > 0),  -- K
  target_generation BIGINT      NOT NULL CHECK (target_generation >= 0),
  status            TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','done','failed')),
  engine_version    TEXT        NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_runs_automaton ON runs (automaton_id);

CREATE TABLE checkpoints (
  run_id        BIGINT   NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
  generation    BIGINT   NOT NULL CHECK (generation >= 0),
  grid          BYTEA    NOT NULL,      -- zlib(raw uint8 bytes, row-major)
  codec         SMALLINT NOT NULL DEFAULT 1,
  grid_hash     BYTEA    NOT NULL,      -- 16 bytes, over the raw bytes
  state_counts  BIGINT[] NOT NULL,      -- length = num_states, e.g. {3920,71,9}
  PRIMARY KEY (run_id, generation)
);

-- The blob is already zlib-compressed, so stop Postgres from trying again.
ALTER TABLE checkpoints ALTER COLUMN grid SET STORAGE EXTERNAL;
