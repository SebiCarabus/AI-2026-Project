export type PresetParameter = {
  type: "integer" | "number";
  default: number;
  minimum?: number;
  maximum?: number;
};

export type PresetMetadata = {
  id: string;
  display_name: string;
  dimensionality: 1 | 2;
  states: Array<{ name: string; color: string }>;
  parameters: Record<string, PresetParameter>;
};

export type RunConfig = {
  preset: string;
  infinite: boolean;
  width: number | null;
  height: number | null;
  parameters: Record<string, number>;
  density: number;
  seed: number | null;
  viewport: Viewport;
  initial_cells?: Array<{ x: number; y: number; state: number }>;
  config_yaml?: string;
  config_hash?: string;
  name?: string;
};

export type ParsedConfig = {
  preset: string;
  infinite: boolean;
  width: number | null;
  height: number | null;
  parameters: Record<string, number>;
  density: number;
  seed: number | null;
  initial_cells?: Array<{ x: number; y: number; state: number }>;
  config_yaml?: string;
  config_hash?: string;
  name?: string;
};

export type ParsedConfigResponse = {
  config: ParsedConfig;
  config_hash: string;
  filename: string;
  config_yaml?: string;
  name?: string | null;
};

export type SnapshotMetadata = {
  id: string | number;
  config_hash: string;
  preset: string;
  display_name: string;
  width: number | null;
  height: number | null;
  counts: Array<number | null>;
  generation: number;
  created_at: string;
};

export type Viewport = {
  x: number;
  y: number;
  width: number;
  height: number;
  padding: number;
};

export type SimulationFrame = {
  id: string;
  preset: string;
  config_hash?: string;
  infinite: boolean;
  generation: number;
  width: number | null;
  height: number | null;
  one_dimensional: boolean;
  states: Array<{ name: string; color: string }>;
  view: Omit<Viewport, "padding">;
  grid_origin: { x: number; y: number };
  counts: Array<number | null>;
  grid: number[][];
};

const API_ROOT = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    throw await parseResponseError(response);
  }

  return response.json() as Promise<T>;
}

async function parseResponseError(response: Response): Promise<Error> {
  const body = (await response.json().catch(() => null)) as
    | { detail?: string | Array<{ msg?: string }> }
    | null;
  const detail = Array.isArray(body?.detail)
    ? body.detail.map((item) => item.msg).filter(Boolean).join("; ")
    : body?.detail;
  return new Error(detail || `Request failed (${response.status})`);
}

export const api = {
  listPresets: () => request<PresetMetadata[]>("/presets"),
  createSimulation: (config: RunConfig) => {
    const { config_yaml: _configYaml, ...payload } = config;
    return request<SimulationFrame>("/simulations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  resetSimulation: (id: string, config: RunConfig) => {
    const { config_yaml: _configYaml, ...payload } = config;
    return request<SimulationFrame>(`/simulations/${id}/reset`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  parseConfig: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API_ROOT}/configs/parse`, { method: "POST", body: form });
    if (!response.ok) throw await parseResponseError(response);
    return response.json() as Promise<ParsedConfigResponse>;
  },
  downloadConfig: async (id: string) => {
    const response = await fetch(`${API_ROOT}/simulations/${encodeURIComponent(id)}/config`);
    if (!response.ok) throw await parseResponseError(response);
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const filename = disposition.match(/filename\*?=(?:UTF-8''|\")?([^;\"]+)/i)?.[1]
      ?.trim()
      .replace(/[\"]$/, "")
      .split("/")
      .pop() || "cellab-config.yaml";
    return { blob: await response.blob(), filename };
  },
  saveSnapshot: (id: string) =>
    request<SnapshotMetadata>(`/simulations/${encodeURIComponent(id)}/snapshots`, { method: "POST" }),
  listSnapshots: (configHash: string) =>
    request<SnapshotMetadata[]>(`/snapshots?config_hash=${encodeURIComponent(configHash)}`),
  restoreSnapshot: (id: string, snapshotId: string | number) =>
    request<SimulationFrame>(`/simulations/${encodeURIComponent(id)}/snapshots/${encodeURIComponent(snapshotId)}/restore`, { method: "POST" }),
};

export type SimulationCommand =
  | { type: "viewport"; x: number; y: number; width: number; height: number; padding: number }
  | { type: "start"; speed: number }
  | { type: "pause" | "step" }
  | { type: "clear" }
  | { type: "paint"; cells: Array<{ x: number; y: number; state: number }> };

export type SimulationStream = {
  send: (command: SimulationCommand) => void;
  close: () => void;
};

export function connectSimulationStream(
  id: string,
  handlers: {
    onOpen?: () => void;
    onFrame: (frame: SimulationFrame) => void;
    onError: (error: Error) => void;
    onClose?: () => void;
  },
): SimulationStream {
  const url = new URL(`${API_ROOT}/simulations/${encodeURIComponent(id)}/stream`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(url);
  const pending: SimulationCommand[] = [];

  function send(command: SimulationCommand) {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(command));
    else if (socket.readyState === WebSocket.CONNECTING) pending.push(command);
  }

  socket.addEventListener("open", () => {
    while (pending.length > 0) socket.send(JSON.stringify(pending.shift()));
    handlers.onOpen?.();
  });
  socket.addEventListener("message", (event) => {
    try {
      const payload = JSON.parse(String(event.data)) as SimulationFrame | { type: "error"; detail?: string };
      if ("type" in payload && payload.type === "error") {
        handlers.onError(new Error(payload.detail ?? "Simulation stream error."));
      } else {
        handlers.onFrame(payload as SimulationFrame);
      }
    } catch {
      handlers.onError(new Error("Received an invalid message from the simulation stream."));
    }
  });
  socket.addEventListener("error", () => handlers.onError(new Error("The simulation WebSocket failed.")));
  socket.addEventListener("close", handlers.onClose ?? (() => undefined));

  return {
    send,
    close: () => socket.close(),
  };
}
