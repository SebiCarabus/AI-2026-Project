# CelLab web API

Run from the `cellab` directory with the repository's root virtual environment:

```sh
../venv/bin/uvicorn web.backend.main:app --reload
```

The API is served under `/api`. `GET /api/presets` returns an array of
`{id, display_name, dimensionality, states, parameters}` entries. `states` is
an array of `{name, color}` values; `parameters` maps configurable parameter
names to metadata such as `{type, default, minimum, maximum}`.

Install `requirements.txt` to include Uvicorn's WebSocket transport dependency.
The Postgres snapshot backend uses psycopg 3. The Compose API connects to
`postgresql://cellab:cellab@db:5432/cellab`; set `CELLAB_CONFIG_MAX_UPLOAD_MB`
to change the YAML upload limit (default: 10 MiB; set a positive whole number,
enforced by the API and Nginx).
When running Uvicorn directly on the host, set
`DATABASE_URL=postgresql://cellab:cellab@localhost:54329/cellab` and start the
Compose `db` service first.

Create a run with `POST /api/simulations` and a JSON body of
`{preset, width, height, infinite, parameters, density, seed, initial_cells}`. `parameters` can omit
values to use preset defaults. `density` is the probability of a non-zero
initial cell; with multiple non-zero states, the chosen state is uniform.
Providing a `seed` makes initial cells and subsequent stochastic steps
reproducible. For `elementary`, `parameters.rule` is the Wolfram rule number,
`width` is the line length, and `height` is returned as `1` regardless of the
submitted value.

Finite worlds use the array representation up to `CELLAB_TREE_THRESHOLD_MB`
(default 150 MiB, selected using an estimated 32 bytes per cell of simulation
working memory). Larger finite worlds and
`infinite: true` use a hash-consed sparse binary tree/quadtree. Tree worlds need
a deterministic rule with a stable state-0 background and neighbourhood reach
at most one. Infinite worlds require `density: 0`; sparse `initial_cells` use
`{x, y, state}` objects in numeric state IDs. Finite tree worlds may use random
initialization only while the sampled non-zero count stays below
`CELLAB_MAX_TREE_CELLS` (default 100,000); dense random fields cannot be stored
sparsely and return a validation error rather than allocating a giant array.

Create, fetch, step, paint, and reset requests return a frame with
`id, preset, config_hash, generation, width, height, infinite, one_dimensional,
states, grid, view, grid_origin, counts`. `grid` uses row-major arrays; 1D runs
are serialized as one row. `counts` lists the full world's number of cells in
each state; the background count is `null` for infinite worlds. `view` is the
visible rectangle and `grid_origin` is the world coordinate at `grid[0][0]`.

Create and reset accept an optional `viewport` object:
`{x, y, width, height, padding}`. The requested view and its padding are
clipped to the world; padding is capped at 64 cells. Frames include only that
clipped rectangle. For elementary (1D) runs, `y` and `height` in the response
are always `0` and `1`. Without a viewport, frames retain the full-world grid
for backward compatibility. The active viewport is retained for subsequent
REST frame and step responses.

`PUT /api/simulations/{id}/grid` accepts `{grid: [[...], ...]}` and does not
advance the generation. `POST /api/simulations/{id}/reset` uses the create body
and preserves the run ID. Runs are in-memory and are cleared when the server
process stops.

Connect to `WS /api/simulations/{id}/stream` for interactive viewport and
simulation updates. Send JSON commands:

- `{"type":"viewport","x":0,"y":0,"width":100,"height":60,"padding":8}`
  sets the active view and immediately returns a cropped frame.
- `{"type":"start","speed":10}` advances at the requested generations per
  second and sends a frame after each generation.
- `{"type":"pause"}` stops timed stepping.
- `{"type":"step"}` advances once and sends a frame.
- `{"type":"paint","cells":[{"x":3,"y":4,"state":1}]}` paints
  world-coordinate cells and sends a frame. Coordinates and state IDs are
  validated against the full world and the preset's states.
- `{"type":"clear"}` clears the world and sends the current cropped frame.

Frames sent over the socket use the same shape and cropping as REST frames.
Invalid commands or paint/viewport values receive an `{type: "error",
detail: "..."}` message; a missing simulation closes with WebSocket code 4404.

Every generation still updates the complete bounded world so off-screen cells
remain correct when the user pans. The backend steps in tiles with neighbour
halos, while sending only the active viewport and its padding to the browser.

## Config YAML and snapshots

`POST /api/configs/parse` accepts multipart field `file` with a UTF-8 YAML v1
run config. The parser rejects unknown keys and returns normalized run settings,
the SHA-256 `config_hash` (over the exact uploaded bytes), and `name`. The API
keeps the original YAML in a bounded short-lived cache so Apply sends only the
normalized settings plus hash. For finite worlds, dimensions are positive integers; 1D
presets use `world.width` and optional `world.height: 1`. Infinite worlds omit
both dimensions, use zero density, and provide an explicit finite `initial.cells`
mapping. Cell coordinates are `[row, col]`, or `[index]`/`[0, index]` for 1D;
cell state names are mapped to the preset's numeric state IDs.

- `GET /api/simulations/{id}/config` downloads the active YAML with
  `Content-Disposition` and `X-Config-Hash` headers.
- `POST /api/simulations/{id}/snapshots` persists a run snapshot and returns
  `{id, config_hash, preset, display_name, width, height, counts, generation,
  created_at}`.
- `GET /api/snapshots?config_hash=...` returns metadata newest first; omitting
  the query returns all metadata rows.
- `POST /api/simulations/{id}/snapshots/{snapshot_id}/restore` restores saved
  state into the active simulation and returns its frame.

The additive `snapshots` PostgreSQL table stores config hash/YAML, preset and
display name, nullable dimensions, counts, generation, creation time, and an
opaque compressed state blob. `state_encoding` carries a `zlib:` compression
tag plus the service-provided payload format; a btree index supports config-hash
filtering. `SnapshotStore.ensure_schema()` runs idempotently so existing database
volumes receive the table/index without removing or recreating volumes.

Live runs remain process-local; saved snapshots persist in Postgres and can be
restored after restarting the API.

The snapshot/config routes use the service's config export and snapshot state
codecs. Array states are stored as compressed uint8 data; tree states store only
non-zero coordinates and state IDs.
