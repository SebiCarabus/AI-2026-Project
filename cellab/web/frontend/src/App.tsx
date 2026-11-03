import { useCallback, useEffect, useRef, useState } from "react";
import BoardCanvas, { type HistoryRow } from "./BoardCanvas";
import { api, connectSimulationStream } from "./api";
import type { ParsedConfig, PresetMetadata, RunConfig, SimulationFrame, SimulationStream, SnapshotMetadata, Viewport } from "./api";

type Controls = {
  preset: string;
  width: string;
  height: string;
  infinite: boolean;
  density: number;
  seed: string;
  parameters: Record<string, number>;
  initial_cells?: Array<{ x: number; y: number; state: number }>;
  config_yaml?: string;
  config_hash?: string;
  name?: string;
  config_filename?: string;
};

const initialControls: Controls = {
  preset: "game_of_life",
  width: "56",
  height: "36",
  infinite: false,
  density: 0.24,
  seed: "",
  parameters: {},
};

const MAX_GENERATION_HISTORY = 100_000;

const parameterLabels: Record<string, string> = {
  rule: "Wolfram rule",
  num_states: "Number of states",
  threshold: "Threshold",
  growth: "Tree growth chance",
  lightning: "Lightning chance",
};

function defaultParameters(preset: PresetMetadata): Record<string, number> {
  return Object.fromEntries(
    Object.entries(preset.parameters).map(([key, metadata]) => [key, metadata.default]),
  );
}

function controlsFromParsedConfig(config: ParsedConfig, preset: PresetMetadata, response: { config_yaml?: string; config_hash: string; name?: string | null; filename: string }): Controls {
  return {
    preset: config.preset,
    width: config.width === null ? "" : String(config.width),
    height: config.height === null ? "" : String(config.height),
    infinite: config.infinite,
    density: config.infinite ? 0 : config.density,
    seed: config.seed === null ? "" : String(config.seed),
    parameters: { ...defaultParameters(preset), ...config.parameters },
    ...(config.initial_cells ? { initial_cells: config.initial_cells } : {}),
    config_yaml: config.config_yaml ?? response.config_yaml,
    config_hash: config.config_hash ?? response.config_hash,
    name: config.name ?? response.name ?? undefined,
    config_filename: response.filename,
  };
}

function TrashIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" width="17" height="17">
      <path d="M4.5 6h11m-9.5 0 .6 10h7.8L15 6M8 6V4h4v2m-3 2.5v5m2-5v5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.4" />
    </svg>
  );
}

function ResetIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" width="17" height="17">
      <path d="M4.3 8a6 6 0 1 1-.1 4m.1-8v4h4M10 6.5v4l2.7 1.7" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function runConfig(controls: Controls, preset: PresetMetadata, requestedViewport: Viewport): RunConfig {
  const seedValue = controls.seed.trim() === "" ? null : Number(controls.seed);
  const worldWidth = controls.infinite ? null : Number(controls.width);
  const worldHeight = controls.infinite ? null : preset.dimensionality === 1 ? 1 : Number(controls.height);
  const x = controls.infinite ? requestedViewport.x : Math.max(0, Math.min(requestedViewport.x, (worldWidth ?? 1) - 1));
  const y = preset.dimensionality === 1 ? 0 : controls.infinite ? requestedViewport.y : Math.max(0, Math.min(requestedViewport.y, (worldHeight ?? 1) - 1));
  const viewWidth = controls.infinite ? requestedViewport.width : Math.max(1, Math.min(requestedViewport.width, (worldWidth ?? 1) - x));
  const viewHeight = controls.infinite
    ? preset.dimensionality === 1 ? 1 : requestedViewport.height
    : preset.dimensionality === 1 ? 1 : Math.max(1, Math.min(requestedViewport.height, (worldHeight ?? 1) - y));
  return {
    preset: controls.preset,
    infinite: controls.infinite,
    width: worldWidth,
    height: worldHeight,
    parameters: controls.parameters,
    density: controls.infinite ? 0 : controls.density,
    seed: seedValue !== null && Number.isFinite(seedValue) ? seedValue : null,
    viewport: { x, y, width: viewWidth, height: viewHeight, padding: requestedViewport.padding },
    ...(controls.initial_cells ? { initial_cells: controls.initial_cells } : {}),
    ...(controls.config_yaml !== undefined ? { config_yaml: controls.config_yaml } : {}),
    ...(controls.config_hash !== undefined ? { config_hash: controls.config_hash } : {}),
    ...(controls.name !== undefined ? { name: controls.name } : {}),
  };
}

function dimensionWarning(controls: Controls): string | null {
  if (controls.infinite) return null;
  for (const [label, value] of [["Width", controls.width], ["Height", controls.height]] as const) {
    const trimmed = value.trim();
    const dimension = Number(trimmed);
    if (!/^\d+$/.test(trimmed) || !Number.isSafeInteger(dimension) || dimension <= 0) {
      return `${label} must be a positive whole number.`;
    }
  }
  return null;
}

function sparseTreeWarning(controls: Controls): string | null {
  if (controls.infinite || dimensionWarning(controls)) return null;
  const cellCount = Number(controls.width) * Number(controls.height);
  const exceedsArrayThreshold = cellCount * 32 > 150 * 1024 * 1024;
  if (exceedsArrayThreshold && cellCount * controls.density > 100_000) {
    return "This size uses sparse tree storage. Set density to 0 or upload a YAML file with sparse initial cells.";
  }
  return null;
}

function cloneGrid(grid: number[][]): number[][] {
  return grid.map((row) => [...row]);
}

function loadColorOverrides(): Record<string, string> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem("cellab-state-colors") ?? "{}");
    if (!saved || typeof saved !== "object") return {};
    return Object.fromEntries(
      Object.entries(saved).filter((entry): entry is [string, string] =>
        typeof entry[1] === "string" && /^#[0-9a-fA-F]{6}$/.test(entry[1]),
      ),
    );
  } catch {
    return {};
  }
}

const paletteColors = [
  "#ffffff", "#aeb8b0", "#111111", "#ff5b5b", "#ff914d",
  "#ffd84d", "#c8f36b", "#4fc77b", "#43c6c6", "#58a6ff",
  "#8171ff", "#d879e8", "#ff84b7", "#855b42",
];

function currentVisibleRow(frame: SimulationFrame): HistoryRow {
  return {
    origin: frame.grid_origin.x,
    generation: frame.generation,
    cells: Uint8Array.from(frame.grid[0] ?? []),
  };
}

function ColorEditor({
  color,
  onChange,
  onClose,
}: {
  color: string;
  onChange: (color: string) => void;
  onClose: () => void;
}) {
  const [hex, setHex] = useState(color.toUpperCase());
  const popupRef = useRef<HTMLDivElement>(null);

  useEffect(() => setHex(color.toUpperCase()), [color]);

  useEffect(() => {
    function closeOnOutsidePointer(event: PointerEvent) {
      if (event.target instanceof Node && !popupRef.current?.contains(event.target)) onClose();
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [onClose]);

  function changeHex(value: string) {
    const normalized = value.startsWith("#") ? value : `#${value}`;
    setHex(normalized.toUpperCase());
    if (/^#[0-9A-F]{6}$/.test(normalized)) onChange(normalized);
  }

  return (
    <div ref={popupRef} className="color-popover" role="dialog" aria-label="Change state color" onPointerDown={(event) => event.stopPropagation()}>
      <div className="color-popover-heading">
        <span>Choose a color</span>
        <button className="color-close" type="button" aria-label="Close color picker" onClick={onClose}>×</button>
      </div>
      <div className="color-palette">
        {paletteColors.map((paletteColor) => (
          <button
            type="button"
            key={paletteColor}
            className={`palette-swatch ${color.toLowerCase() === paletteColor ? "palette-selected" : ""}`}
            style={{ backgroundColor: paletteColor }}
            aria-label={`Use ${paletteColor}`}
            title={paletteColor}
            onClick={() => { setHex(paletteColor.toUpperCase()); onChange(paletteColor); }}
          />
        ))}
      </div>
      <label className="hex-color-field">
        <span>HEX COLOR</span>
        <input value={hex} maxLength={7} spellCheck={false} onChange={(event) => changeHex(event.target.value)} />
        <input
          className="native-color-input"
          type="color"
          value={/^#[0-9A-F]{6}$/.test(hex) ? hex : color}
          aria-label="Pick a custom color"
          onChange={(event) => { setHex(event.target.value.toUpperCase()); onChange(event.target.value); }}
        />
      </label>
      <button className="color-done" type="button" onClick={onClose}>Done</button>
    </div>
  );
}

export default function App() {
  const [presets, setPresets] = useState<PresetMetadata[]>([]);
  const [controls, setControls] = useState<Controls>(initialControls);
  const [activeControls, setActiveControls] = useState<Controls | null>(null);
  const [frame, setFrame] = useState<SimulationFrame | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [brush, setBrush] = useState(1);
  const [speed, setSpeed] = useState(8);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [streamReady, setStreamReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [editingColor, setEditingColor] = useState<string | null>(null);
  const [colorOverrides, setColorOverrides] = useState<Record<string, string>>(loadColorOverrides);
  const [snapshots, setSnapshots] = useState<SnapshotMetadata[]>([]);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [configBusy, setConfigBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const frameRef = useRef<SimulationFrame | null>(null);
  const receiveFrameRef = useRef<(frame: SimulationFrame, resetHistory?: boolean, preserveConfigHash?: boolean) => void>(() => undefined);
  const viewportRef = useRef<Viewport>({ x: 0, y: 0, width: 48, height: 32, padding: 64 });
  const streamRef = useRef<SimulationStream | null>(null);
  const pendingPaintRef = useRef<Map<string, { x: number; y: number; state: number }>>(new Map());
  const requestVersionRef = useRef(0);

  function updateDraft(update: (previous: Controls) => Controls) {
    setControls((previous) => {
      const next = update(previous);
      return {
        ...next,
        config_yaml: undefined,
        config_hash: undefined,
        name: undefined,
        config_filename: undefined,
      };
    });
  }

  const selectedPreset = presets.find((preset) => preset.id === controls.preset);
  const activePreset = presets.find((preset) => preset.id === frame?.preset) ?? selectedPreset;
  const displayFrame = frame
    ? {
        ...frame,
        states: frame.states.map((state) => ({
          ...state,
          color: colorOverrides[`${frame.preset}:${state.name}`] ?? state.color,
        })),
      }
    : null;

  const receiveFrame = useCallback((incoming: SimulationFrame, resetHistory = false, preserveConfigHash = true) => {
    const previous = frameRef.current;
    const next = incoming.config_hash || !preserveConfigHash || !previous?.config_hash
      ? incoming
      : { ...incoming, config_hash: previous.config_hash };
    const viewportChanged = previous && (
      previous.view.x !== next.view.x || previous.view.width !== next.view.width
    );
    frameRef.current = next;
    setFrame(next);
    if (!next.one_dimensional) {
      setHistory([]);
      return;
    }
    const row = currentVisibleRow(next);
    if (resetHistory || !previous?.one_dimensional || viewportChanged || next.generation < (previous?.generation ?? -1)) {
      setHistory([row]);
    } else if (next.generation > previous.generation) {
      setHistory((rows) => [...rows, row].slice(-MAX_GENERATION_HISTORY));
    } else {
      setHistory((rows) => rows.length === 0 ? [row] : [...rows.slice(0, -1), row]);
    }
  }, [activeControls?.height, controls.height]);
  receiveFrameRef.current = receiveFrame;

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    api.listPresets()
      .then((items) => {
        if (cancelled) return;
        setPresets(items);
        const preset = items.find((item) => item.id === initialControls.preset) ?? items[0];
        if (!preset) throw new Error("The API returned no presets.");
        const nextControls = { ...initialControls, preset: preset.id, parameters: defaultParameters(preset) };
        setControls(nextControls);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not connect to the API.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Metadata is loaded immediately, but the simulation is created only when Apply is pressed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applySetup = useCallback(async (nextControls: Controls, preset: PresetMetadata | undefined) => {
    if (!preset) return;
    if (dimensionWarning(nextControls)) {
      setIsPlaying(false);
      streamRef.current?.send({ type: "pause" });
      return;
    }
    setControls(nextControls);
    setIsPlaying(false);
    streamRef.current?.send({ type: "pause" });
    setIsLoading(true);
    setError(null);
    const version = ++requestVersionRef.current;
    try {
      const config = runConfig(nextControls, preset, viewportRef.current);
      viewportRef.current = config.viewport;
      const current = frameRef.current;
      const next = current
        ? await api.resetSimulation(current.id, config)
        : await api.createSimulation(config);
      if (version === requestVersionRef.current) {
        setActiveControls(nextControls);
        receiveFrame({ ...next, config_hash: next.config_hash ?? config.config_hash }, true, false);
        setBrush(Math.min(1, next.states.length - 1));
        streamRef.current?.send({ type: "viewport", ...config.viewport });
      }
    } catch (reason) {
      if (version === requestVersionRef.current) {
        setError(reason instanceof Error ? reason.message : "Could not reset the simulation.");
      }
    } finally {
      if (version === requestVersionRef.current) setIsLoading(false);
    }
  }, [receiveFrame]);

  const advanceOne = useCallback(() => {
    setIsPlaying(false);
    streamRef.current?.send({ type: "pause" });
    streamRef.current?.send({ type: "step" });
  }, []);

  const handleViewportChange = useCallback((viewport: Viewport) => {
    viewportRef.current = viewport;
    streamRef.current?.send({ type: "viewport", ...viewport });
  }, []);

  useEffect(() => {
    const current = frame;
    if (!current) return;
    setStreamReady(false);
    let stream: SimulationStream;
    stream = connectSimulationStream(current.id, {
      onOpen: () => {
        setStreamReady(true);
        stream.send({ type: "viewport", ...viewportRef.current });
      },
      onFrame: (next) => {
        pendingPaintRef.current.clear();
        receiveFrameRef.current(next);
        setError(null);
      },
      onError: (reason) => setError(reason.message),
      onClose: () => setStreamReady(false),
    });
    streamRef.current = stream;
    return () => {
      if (streamRef.current === stream) streamRef.current = null;
      stream.close();
      setStreamReady(false);
    };
  }, [frame?.id]);

  useEffect(() => {
    if (!streamReady) return;
    streamRef.current?.send(isPlaying ? { type: "start", speed } : { type: "pause" });
  }, [isPlaying, speed, streamReady]);

  useEffect(() => {
    function handleSpacebar(event: KeyboardEvent) {
      const target = event.target;
      if (
        event.code !== "Space" ||
        event.repeat ||
        target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(target.tagName))
      ) return;
      event.preventDefault();
      setIsPlaying((playing) => !playing);
    }
    window.addEventListener("keydown", handleSpacebar);
    return () => window.removeEventListener("keydown", handleSpacebar);
  }, []);

  function paintCell(x: number, y: number, state: number) {
    const current = frameRef.current;
    if (!current) return;
    const nextGrid = cloneGrid(current.grid);
    const gridX = x - current.grid_origin.x;
    const gridY = y - current.grid_origin.y;
    const oldState = nextGrid[gridY]?.[gridX];
    if (oldState === undefined) return;
    nextGrid[gridY][gridX] = state;
    const counts = [...current.counts];
    if (oldState !== state) {
      if (counts[oldState] !== null) counts[oldState] = (counts[oldState] ?? 0) - 1;
      if (counts[state] !== null) counts[state] = (counts[state] ?? 0) + 1;
    }
    const nextFrame = { ...current, grid: nextGrid, counts };
    frameRef.current = nextFrame;
    setFrame(nextFrame);
    pendingPaintRef.current.set(`${x}:${y}`, { x, y, state });
    if (current.one_dimensional) {
      setHistory((rows) => {
        const nextRows = [...rows];
        if (nextRows.length === 0) nextRows.push(currentVisibleRow(nextFrame));
        else {
          const last = nextRows[nextRows.length - 1];
          const cells = last.cells.slice();
          const offset = x - last.origin;
          if (offset >= 0 && offset < cells.length) cells[offset] = state;
          nextRows[nextRows.length - 1] = { ...last, cells };
        }
        return nextRows;
      });
    }
  }

  function finishPaint() {
    const cells = [...pendingPaintRef.current.values()];
    pendingPaintRef.current.clear();
    if (cells.length > 0) streamRef.current?.send({ type: "paint", cells });
  }

  function clearBoard() {
    const current = frameRef.current;
    if (!current) return;
    setIsPlaying(false);
    streamRef.current?.send({ type: "pause" });
    const cleared = current.grid.map((row) => row.map(() => 0));
    const counts: Array<number | null> = current.counts.map(() => 0);
    counts[0] = current.infinite ? null : (current.width ?? 0) * (current.height ?? 0);
    const next = { ...current, grid: cleared, counts };
    frameRef.current = next;
    setFrame(next);
    if (current.one_dimensional) setHistory([currentVisibleRow(next)]);
    streamRef.current?.send({ type: "clear" });
  }

  function handlePresetChange(presetId: string) {
    const preset = presets.find((item) => item.id === presetId);
    if (!preset) return;
    updateDraft((previous) => ({ ...previous, preset: presetId, parameters: defaultParameters(preset), initial_cells: undefined }));
  }

  function handleDimensionChange(key: "width" | "height", value: string) {
    updateDraft((previous) => ({ ...previous, [key]: value }));
  }

  function changeStateColor(stateName: string, color: string) {
    if (!/^#[0-9a-fA-F]{6}$/.test(color)) return;
    const key = `${frameRef.current?.preset ?? controls.preset}:${stateName}`;
    setColorOverrides((previous) => {
      const next = { ...previous, [key]: color };
      localStorage.setItem("cellab-state-colors", JSON.stringify(next));
      return next;
    });
  }

  const counts = displayFrame
    ? displayFrame.states.map((state, stateIndex) => ({
        ...state,
        count: displayFrame.counts[stateIndex] === null
          ? null
          : displayFrame.counts[stateIndex] ?? 0,
      }))
    : [];
  const isOneDimensional = frame?.one_dimensional ?? selectedPreset?.dimensionality === 1;
  const draftIsOneDimensional = selectedPreset?.dimensionality === 1;
  const invalidDimensions = dimensionWarning(controls);
  const largeDenseWarning = sparseTreeWarning(controls);
  const activeConfigHash = frame?.config_hash;

  useEffect(() => {
    if (!activeConfigHash) {
      setSnapshots([]);
      setSnapshotsLoading(false);
      return;
    }
    let cancelled = false;
    setSnapshotsLoading(true);
    api.listSnapshots(activeConfigHash)
      .then((items) => { if (!cancelled) setSnapshots(items); })
      .catch((reason: unknown) => {
        if (!cancelled) setActionError(reason instanceof Error ? reason.message : "Could not load snapshots.");
      })
      .finally(() => { if (!cancelled) setSnapshotsLoading(false); });
    return () => { cancelled = true; };
  }, [activeConfigHash]);

  async function uploadConfig(file: File) {
    setConfigBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const parsed = await api.parseConfig(file);
      const preset = presets.find((item) => item.id === parsed.config.preset);
      if (!preset) throw new Error(`The uploaded config uses unknown preset “${parsed.config.preset}”.`);
      setControls(controlsFromParsedConfig(parsed.config, preset, { ...parsed, filename: parsed.filename || file.name }));
      setActionNotice(`Loaded ${parsed.filename || file.name} into draft settings. Apply to create the world.`);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not parse the uploaded config.");
    } finally {
      setConfigBusy(false);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
    }
  }

  async function downloadCurrentConfig() {
    if (!frame) return;
    setConfigBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const downloaded = await api.downloadConfig(frame.id);
      const url = URL.createObjectURL(downloaded.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = downloaded.filename;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setActionNotice(`Downloaded ${downloaded.filename}.`);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not download the current config.");
    } finally {
      setConfigBusy(false);
    }
  }

  async function saveSnapshot() {
    if (!frame) return;
    setSnapshotBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const saved = await api.saveSnapshot(frame.id);
      setSnapshots((previous) => [saved, ...previous.filter((item) => item.id !== saved.id)]);
      const current = frameRef.current;
      if (current?.id === frame.id) receiveFrame({ ...current, config_hash: saved.config_hash });
      setActionNotice(`Saved snapshot ${saved.id}.`);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not save the snapshot.");
    } finally {
      setSnapshotBusy(false);
    }
  }

  async function restoreSnapshot(snapshot: SnapshotMetadata) {
    if (!frame) return;
    setIsPlaying(false);
    streamRef.current?.send({ type: "pause" });
    setSnapshotBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const restored = await api.restoreSnapshot(frame.id, snapshot.id);
      receiveFrame(restored, true);
      streamRef.current?.send({ type: "viewport", ...viewportRef.current });
      setActionNotice(`Restored snapshot ${snapshot.id}.`);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not restore the snapshot.");
    } finally {
      setSnapshotBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><i /><i /><i /><i /></div>
          <span className="brand-name">cellab</span>
          <span className="brand-divider" />
          <span className="brand-section">AUTOMATA STUDIO</span>
        </div>
        <div className={`connection-pill ${error ? "connection-offline" : isLoading ? "connection-loading" : ""}`}>
          <span className="connection-dot" />
          {error ? "API unavailable" : isLoading ? "Connecting" : frame ? "API connected" : "API ready"}
        </div>
      </header>

      <main className="workspace">
        <aside className="sidebar">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">CONFIGURATION</span>
              <h1>Build a world</h1>
            </div>
            <span className="heading-spark" aria-hidden="true">✳</span>
          </div>

          <section className="control-section">
            <label className="field-label" htmlFor="preset-select">Preset automaton</label>
            <div className="select-wrap">
              <select
                id="preset-select"
                value={controls.preset}
                onChange={(event) => handlePresetChange(event.target.value)}
                disabled={!presets.length || isLoading}
              >
                {presets.map((preset) => <option value={preset.id} key={preset.id}>{preset.display_name}</option>)}
              </select>
              <span aria-hidden="true">⌄</span>
            </div>
            <p className="field-hint">
              {draftIsOneDimensional ? "One-dimensional · fixed-zero boundary" : "Two-dimensional · wrap-around edges"}
            </p>
          </section>

          {selectedPreset && Object.keys(selectedPreset.parameters).length > 0 && (
            <section className="control-section parameter-section">
              <div className="section-title-row">
                <span className="field-label">Rule parameters</span>
                <span className="tiny-tag">PRESET</span>
              </div>
              <div className="parameter-grid">
                {Object.entries(selectedPreset.parameters).map(([key, metadata]) => (
                  <label className="field-label parameter-field" key={key}>
                    <span>{parameterLabels[key] ?? key.replaceAll("_", " ")}</span>
                    <input
                      type="number"
                      min={metadata.minimum}
                      max={metadata.maximum}
                      step={metadata.type === "integer" ? 1 : 0.001}
                      value={controls.parameters[key] ?? metadata.default}
                      onChange={(event) => updateDraft((previous) => ({
                        ...previous,
                        parameters: { ...previous.parameters, [key]: Number(event.target.value) },
                      }))}
                    />
                  </label>
                ))}
              </div>
            </section>
          )}

          <section className="control-section">
            <div className="section-title-row">
              <span className="field-label">World dimensions</span>
              <span className="unit-tag">CELLS</span>
            </div>
            <label className="infinite-toggle">
              <input
                type="checkbox"
                checked={controls.infinite}
                disabled={controls.preset === "forest_fire"}
                onChange={(event) => updateDraft((previous) => ({
                  ...previous,
                  infinite: event.target.checked,
                  density: event.target.checked ? 0 : previous.density,
                }))}
              />
              <span><strong>Infinite sparse world</strong><small>{controls.preset === "forest_fire" ? "Requires a deterministic preset" : "Unbounded space · start from sparse cells"}</small></span>
            </label>
            <div className="dimension-row">
              <label className="number-field">
                <span>{draftIsOneDimensional ? "Line width" : "Width"}</span>
                <input type="text" inputMode="numeric" pattern="[0-9]*" value={controls.infinite ? "" : controls.width} placeholder={controls.infinite ? "Unbounded" : undefined} disabled={controls.infinite} onChange={(event) => handleDimensionChange("width", event.target.value)} aria-invalid={!controls.infinite && Boolean(invalidDimensions?.startsWith("Width"))} />
              </label>
              <span className="dimension-x">×</span>
              <label className="number-field">
                <span>{draftIsOneDimensional ? "History rows" : "Height"}</span>
                <input type="text" inputMode="numeric" pattern="[0-9]*" value={controls.infinite ? "" : controls.height} placeholder={controls.infinite ? "Unbounded" : undefined} disabled={controls.infinite} onChange={(event) => handleDimensionChange("height", event.target.value)} aria-invalid={!controls.infinite && Boolean(invalidDimensions?.startsWith("Height"))} />
              </label>
            </div>
            {invalidDimensions && <p className="dimension-warning" role="alert">{invalidDimensions}</p>}
            {largeDenseWarning && <p className="dimension-warning" role="status">{largeDenseWarning}</p>}
          </section>

          <section className="control-section init-section">
            <div className="section-title-row">
              <span className="field-label">Initial state</span>
              <span className="unit-tag">RANDOM + PAINT</span>
            </div>
            <label className="slider-label" htmlFor="density-slider">
              <span>{controls.infinite ? "Active density · sparse cells only" : "Active density"}</span>
              <strong>{Math.round(controls.density * 100)}%</strong>
            </label>
            <input
              id="density-slider"
              className="density-slider"
              type="range"
              min="0"
              max="100"
              value={Math.round(controls.density * 100)}
              disabled={controls.infinite}
              onChange={(event) => updateDraft((previous) => ({ ...previous, density: Number(event.target.value) / 100 }))}
            />
            <label className="field-label seed-field">
              Random seed <span className="optional-label">OPTIONAL</span>
              <input
                type="number"
                min="0"
                step="1"
                placeholder="New seed each reset"
                value={controls.seed}
                onChange={(event) => updateDraft((previous) => ({ ...previous, seed: event.target.value }))}
              />
            </label>
            <div className="world-actions">
              <button className="button button-subtle" type="button" onClick={() => void applySetup(controls, selectedPreset)} disabled={!selectedPreset || isLoading}>
                <span aria-hidden="true">⟳</span> Randomize
              </button>
              <button className="button button-subtle" type="button" onClick={clearBoard} disabled={!frame || isLoading}>
                <span aria-hidden="true">□</span> Clear
              </button>
            </div>
          </section>

          <section className="control-section brush-section">
            <div className="section-title-row">
              <span className="field-label">Paint cells</span>
              <span className="paint-hint">PAINT · EDIT COLOR</span>
            </div>
            <div className="brush-list">
              {(displayFrame?.states ?? selectedPreset?.states ?? []).map((state, index) => {
                const colorKey = `${displayFrame?.preset ?? controls.preset}:${state.name}`;
                const color = colorOverrides[colorKey] ?? state.color;
                return (
                  <div className="brush-row" key={`${state.name}-${index}`}>
                    <button
                      className={`brush-option ${brush === index ? "brush-selected" : ""}`}
                      type="button"
                      onClick={() => setBrush(index)}
                      aria-pressed={brush === index}
                    >
                      <span>{state.name}</span>
                      {brush === index && <span className="brush-check" aria-hidden="true">✓</span>}
                    </button>
                    <button
                      className="color-trigger"
                      type="button"
                      style={{ backgroundColor: color }}
                      aria-label={`Change ${state.name} color`}
                      title={`Change ${state.name} color`}
                      onClick={() => setEditingColor(editingColor === state.name ? null : state.name)}
                    />
                    {editingColor === state.name && (
                      <ColorEditor
                        color={color}
                        onChange={(nextColor) => changeStateColor(state.name, nextColor)}
                        onClose={() => setEditingColor(null)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </section>

          <p className="apply-note">Changes stay pending until Apply or Randomize.</p>
          <button className="button button-primary apply-button" type="button" onClick={() => void applySetup(controls, selectedPreset)} disabled={!selectedPreset || isLoading}>
            {isLoading ? <span className="spinner" /> : <span aria-hidden="true">↻</span>}
            Apply setup
          </button>
        </aside>

        <section className="simulation-stage">
          <div className="stage-heading">
            <div>
              <div className="eyebrow stage-eyebrow">LIVE SIMULATION <span className="eyebrow-line" /></div>
              <h2>{activePreset?.display_name ?? "Cellular Automaton"}</h2>
              <p className="stage-subtitle">{isOneDimensional ? "Space-time diagram" : "Every cell updates simultaneously"}</p>
            </div>
            <div className="generation-stat">
              <span>GENERATION</span>
              <strong>{String(frame?.generation ?? 0).padStart(4, "0")}</strong>
            </div>
          </div>

          {error && <div className="error-banner" role="alert"><span>!</span> {error}</div>}

          <section className="config-snapshot-card" aria-label="Configuration and snapshots">
            <div className="config-tools-row">
              <div>
                <span className="eyebrow">CONFIG FILE</span>
                <p>{controls.config_filename ? `${controls.config_filename} · staged draft` : "Import or export a YAML configuration"}</p>
              </div>
              <div className="config-buttons">
                <input
                  ref={uploadInputRef}
                  className="visually-hidden"
                  type="file"
                  accept=".yaml,.yml,application/yaml,text/yaml,text/plain"
                  aria-label="Upload YAML configuration"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void uploadConfig(file);
                  }}
                />
                <button className="button button-subtle" type="button" onClick={() => uploadInputRef.current?.click()} disabled={configBusy || isLoading}>
                  {configBusy ? <span className="spinner" /> : <span aria-hidden="true">↑</span>} Upload YAML
                </button>
                <button className="button button-subtle" type="button" onClick={() => void downloadCurrentConfig()} disabled={!frame || configBusy || isLoading}>
                  <span aria-hidden="true">↓</span> Download current
                </button>
              </div>
            </div>
            <div className="snapshots-area">
              <div className="snapshots-heading">
                <div>
                  <span className="eyebrow">SAVED SNAPSHOTS</span>
                  <p>{activeConfigHash ? `Filtered by active config · ${activeConfigHash.slice(0, 12)}` : "Apply a config with a hash to view matching snapshots"}</p>
                </div>
                <button className="button button-subtle snapshot-save" type="button" onClick={() => void saveSnapshot()} disabled={!frame || snapshotBusy || isLoading}>
                  {snapshotBusy ? <span className="spinner" /> : <span aria-hidden="true">＋</span>} Save current
                </button>
              </div>
              {actionError && <p className="action-feedback action-error" role="alert">{actionError}</p>}
              {actionNotice && <p className="action-feedback" role="status">{actionNotice}</p>}
              {snapshotsLoading ? <p className="snapshot-empty">Loading matching snapshots…</p> : snapshots.length === 0 ? (
                <p className="snapshot-empty">{activeConfigHash ? "No snapshots saved for this configuration yet." : "Snapshots are grouped by the active configuration hash."}</p>
              ) : (
                <div className="snapshot-list">
                  {snapshots.map((snapshot) => {
                    const snapshotPreset = presets.find((preset) => preset.id === snapshot.preset);
                    const snapshotCounts = snapshot.counts.map((count, index) => `${snapshotPreset?.states[index]?.name ?? `State ${index}`}: ${count === null ? "∞" : count.toLocaleString()}`).join(" · ");
                    const snapshotDimensions = snapshot.width === null || snapshot.height === null
                      ? "Infinite"
                      : `${snapshot.width} × ${snapshot.height}`;
                    return (
                      <article className="snapshot-row" key={snapshot.id}>
                        <div className="snapshot-details">
                          <strong>{snapshot.display_name} <span>#{snapshot.id}</span></strong>
                          <p>{snapshot.preset} · {snapshotDimensions} · Gen {snapshot.generation.toLocaleString()}</p>
                          <small>{snapshotCounts || "No cell counts"}</small>
                        </div>
                        <button className="button button-subtle" type="button" onClick={() => void restoreSnapshot(snapshot)} disabled={!frame || snapshotBusy || isLoading}>
                          Restore
                        </button>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          <div className="board-card">
            <div className="board-toolbar">
              <div className="world-meta">
                <span className="world-icon" aria-hidden="true">▦</span>
                <span>{frame ? frame.infinite ? "Infinite world" : `${frame.width} × ${frame.height}` : controls.infinite ? "Infinite world · draft" : `${controls.width} × ${draftIsOneDimensional ? "1D" : controls.height}`}</span>
                <span className="meta-divider" />
                <span>{isOneDimensional ? "1D LINE" : "2D GRID"}</span>
              </div>
              <div className="board-tools">
                <button className="icon-button" type="button" title="Clear board" aria-label="Clear board" onClick={clearBoard} disabled={!frame || isLoading}>
                  <TrashIcon />
                </button>
                <button className="icon-button" type="button" title="Reset simulation" aria-label="Reset simulation" onClick={() => void applySetup(controls, selectedPreset)} disabled={!frame || isLoading}>
                  <ResetIcon />
                </button>
              </div>
            </div>

            <div className={`board-area ${isOneDimensional ? "board-area-line" : ""}`}>
              {frame ? (
                <BoardCanvas
                  frame={displayFrame ?? frame}
                  history={history}
                  historyLimit={Number(activeControls?.height ?? controls.height)}
                  brush={brush}
                  zoom={zoom}
                  onZoomChange={setZoom}
                  onViewportChange={handleViewportChange}
                  onEditStart={() => {
                    setIsPlaying(false);
                    streamRef.current?.send({ type: "pause" });
                  }}
                  onPaint={paintCell}
                  onEditEnd={finishPaint}
                />
              ) : (
                <div className="loading-state">
                  {isLoading ? <><span className="spinner" /> Loading preset metadata…</> : (
                    <div className="empty-world-callout">
                      <span className="empty-world-icon" aria-hidden="true">▦</span>
                      <strong>Your world is ready to begin</strong>
                      <span>Review the settings, then press <b>Apply setup</b> to generate the first world.</span>
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="board-footer">
              <span><i className="footer-dot" /> {frame ? "WORLD ACTIVE" : isLoading ? "LOADING PRESETS" : "NO WORLD RENDERED"}</span>
              <span>{!streamReady ? "CONNECTING LIVE STREAM…" : isOneDimensional ? "NEW GENERATIONS FLOW DOWNWARD" : "SYNCHRONOUS UPDATE"}</span>
            </div>
          </div>

          <div className="playback-card">
            <div className="playback-controls">
              <button
                className="button button-play"
                type="button"
                onClick={() => setIsPlaying((playing) => !playing)}
                disabled={!frame || isLoading}
              >
                <span aria-hidden="true">{isPlaying ? "Ⅱ" : "▶"}</span>
                {isPlaying ? "Pause" : "Play"}
              </button>
              <button className="button button-step" type="button" onClick={() => { setIsPlaying(false); void advanceOne(); }} disabled={!frame || isLoading}>
                Step <span aria-hidden="true">→</span>
              </button>
              <div className="playback-divider" />
              <label className="speed-control">
                <span className="speed-icon" aria-hidden="true">◷</span>
                <span>Speed</span>
                <input type="range" min="1" max="20" value={speed} onChange={(event) => setSpeed(Number(event.target.value))} />
                <strong>{speed}<small> gen/s</small></strong>
              </label>
            </div>
            <div className="playback-note"><span className="keyboard-key">SPACE</span> {isPlaying ? "to pause" : "to play"}</div>
          </div>

          <div className="state-summary">
            <div className="summary-heading">
              <span className="eyebrow">STATE DISTRIBUTION</span>
              <span>{frame ? frame.infinite ? "INFINITE WORLD" : `${(frame.width ?? 0) * (frame.height ?? 0)} CELLS` : "—"}</span>
            </div>
            <div className="state-stats">
              {counts.map((state) => (
                <div className="state-stat" key={state.name}>
                  <span className="state-swatch" style={{ backgroundColor: state.color }} />
                  <span className="state-stat-name">{state.name}</span>
                  <strong>{state.count === null ? "∞" : state.count.toLocaleString()}</strong>
                  <span className="state-percent">{frame?.infinite ? "—" : frame && (frame.width ?? 0) * (frame.height ?? 0) > 0 ? `${Math.round((state.count ?? 0) / ((frame.width ?? 0) * (frame.height ?? 0)) * 100)}%` : "0%"}</span>
                </div>
              ))}
            </div>
          </div>
          <footer className="stage-footer"><span>CELLAB / PRESET LAB</span><span>SIMULATIONS ARE FINITE & SYNCHRONOUS</span></footer>
        </section>
      </main>
    </div>
  );
}
