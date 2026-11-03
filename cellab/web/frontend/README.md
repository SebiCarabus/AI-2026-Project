# CelLab frontend

React + Vite interface for the preset cellular automata. The UI talks to the FastAPI
service under `/api`; Vite proxies those requests to `http://localhost:8000` during
development.

From this directory:

```sh
npm install
npm run dev
```

Start the API in another terminal from `cellab/`:

```sh
../venv/bin/uvicorn web.backend.main:app --reload
```

The browser requests a visible viewport plus a navigation buffer and renders only that
tile. A WebSocket carries playback commands, paint edits, viewport changes, and pushed
generation frames; 1D space-time history is kept in the browser for the current view.

Configuration inputs are drafts until **Apply setup** is pressed. YAML files can be
uploaded into those draft inputs or downloaded from the active run. Snapshots are listed
for the active configuration hash and can be restored into the current run. The viewport
navigation pad supports press-and-hold panning, and its X/Y fields set the camera origin
directly while retaining the 64-cell server buffer around the visible area.
