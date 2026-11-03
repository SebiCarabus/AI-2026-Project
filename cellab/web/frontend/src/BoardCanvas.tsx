import { useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import type { SimulationFrame, Viewport } from "./api";

export type HistoryRow = { origin: number; generation: number; cells: Uint8Array };

type BoardCanvasProps = {
  frame: SimulationFrame;
  history: HistoryRow[];
  historyLimit: number;
  brush: number;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  onViewportChange: (viewport: Viewport) => void;
  onEditStart: () => void;
  onPaint: (x: number, y: number, state: number) => void;
  onEditEnd: () => void;
};

const VIEWPORT_PADDING = 64;
const TARGET_CELL_SIZE = 16;

export default function BoardCanvas({
  frame,
  history,
  historyLimit,
  brush,
  zoom,
  onZoomChange,
  onViewportChange,
  onEditStart,
  onPaint,
  onEditEnd,
}: BoardCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const paintingRef = useRef(false);
  const lastPaintRef = useRef("");
  const lastViewportKeyRef = useRef("");
  const panFrameRef = useRef<number | null>(null);
  const panDirectionRef = useRef<"up" | "down" | "left" | "right" | null>(null);
  const panStartedAtRef = useRef(0);
  const panLastStepRef = useRef(0);
  const panElapsedRef = useRef(0);
  const [camera, setCamera] = useState({ x: frame.view.x, y: frame.view.y });
  const [originInputs, setOriginInputs] = useState({ x: String(frame.view.x), y: String(frame.view.y) });
  const [timelineTop, setTimelineTop] = useState(0);
  const [screen, setScreen] = useState({ width: 720, height: 420 });

  const cellSize = Math.max(8, Math.round(TARGET_CELL_SIZE * zoom));
  const worldWidth = frame.width ?? Number.POSITIVE_INFINITY;
  const worldHeight = frame.height ?? Number.POSITIVE_INFINITY;
  const visibleWidth = Math.max(1, Math.min(worldWidth, Math.floor(Math.max(1, screen.width - 36) / cellSize)));
  const visibleHeight = frame.one_dimensional
    ? 1
    : Math.max(1, Math.min(worldHeight, Math.floor(Math.max(1, screen.height - 36) / cellSize)));
  const viewX = frame.infinite ? camera.x : Math.max(0, Math.min(camera.x, worldWidth - visibleWidth));
  const viewY = frame.one_dimensional ? 0 : frame.infinite ? camera.y : Math.max(0, Math.min(camera.y, worldHeight - visibleHeight));
  const cameraRef = useRef({ x: viewX, y: viewY });
  cameraRef.current = { x: viewX, y: viewY };
  const timelineRows = frame.one_dimensional
    ? Math.max(1, Math.min(historyLimit, history.length, Math.floor(Math.max(1, screen.height - 36) / cellSize)))
    : visibleHeight;
  const maxTimelineTop = Math.max(0, history.length - timelineRows);
  const timelineGeneration = history[timelineTop]?.generation ?? frame.generation;

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const measure = () => setScreen({ width: viewport.clientWidth, height: viewport.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setCamera((previous) => ({
      x: frame.infinite ? previous.x : Math.max(0, Math.min(previous.x, Math.max(0, worldWidth - visibleWidth))),
      y: frame.one_dimensional ? 0 : frame.infinite ? previous.y : Math.max(0, Math.min(previous.y, Math.max(0, worldHeight - visibleHeight))),
    }));
  }, [frame.width, frame.height, frame.infinite, frame.one_dimensional, visibleWidth, visibleHeight, worldWidth, worldHeight]);

  useEffect(() => {
    if (frame.one_dimensional) {
      setTimelineTop((previous) => {
        const wasFollowing = previous + timelineRows >= history.length - 1;
        return wasFollowing ? maxTimelineTop : Math.min(previous, maxTimelineTop);
      });
    } else {
      setTimelineTop(0);
    }
  }, [frame.one_dimensional, history.length, maxTimelineTop, timelineRows]);

  useEffect(() => {
    setOriginInputs({ x: String(viewX), y: String(frame.one_dimensional ? timelineGeneration : viewY) });
  }, [viewX, viewY, frame.one_dimensional, timelineGeneration]);

  useEffect(() => () => {
    if (panFrameRef.current !== null) window.cancelAnimationFrame(panFrameRef.current);
  }, []);

  useEffect(() => {
    const key = `${frame.id}:${frame.width}:${frame.height}:${frame.one_dimensional}:${viewX}:${viewY}:${visibleWidth}:${visibleHeight}`;
    const tileHeight = frame.grid.length;
    const tileWidth = frame.grid[0]?.length ?? 0;
    const coveredByBuffer =
      viewX >= frame.grid_origin.x &&
      viewX + visibleWidth <= frame.grid_origin.x + tileWidth &&
      viewY >= frame.grid_origin.y &&
      viewY + visibleHeight <= frame.grid_origin.y + tileHeight;
    if (coveredByBuffer) return;
    if (key === lastViewportKeyRef.current) return;
    lastViewportKeyRef.current = key;
    onViewportChange({
      x: viewX,
      y: viewY,
      width: visibleWidth,
      height: visibleHeight,
      padding: VIEWPORT_PADDING,
    });
  }, [frame.id, frame.width, frame.height, frame.one_dimensional, frame.grid, frame.grid_origin, viewX, viewY, visibleWidth, visibleHeight, onViewportChange]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const drawRows = frame.one_dimensional ? history.slice(timelineTop, timelineTop + timelineRows) : [];
    const canvasRows = frame.one_dimensional ? Math.max(1, drawRows.length) : visibleHeight;
    const cssWidth = visibleWidth * cellSize;
    const cssHeight = canvasRows * cellSize;
    canvas.width = Math.max(1, Math.floor(cssWidth * pixelRatio));
    canvas.height = Math.max(1, Math.floor(cssHeight * pixelRatio));
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.fillStyle = "#101713";
    context.fillRect(0, 0, cssWidth, cssHeight);

    const paintCell = (state: number, column: number, row: number) => {
      context.fillStyle = frame.states[state]?.color ?? "#ff4f32";
      context.fillRect(column * cellSize, row * cellSize, cellSize, cellSize);
      if (cellSize >= 7) {
        context.strokeStyle = "rgba(8, 15, 11, 0.42)";
        context.lineWidth = 1;
        context.strokeRect(column * cellSize + 0.5, row * cellSize + 0.5, cellSize - 1, cellSize - 1);
      }
    };

    if (frame.one_dimensional) {
      drawRows.forEach((line, row) => {
        for (let column = 0; column < visibleWidth; column += 1) {
          const state = line.cells[viewX + column - line.origin];
          if (state !== undefined) paintCell(state, column, row);
        }
      });
      if (drawRows.length === 0) {
        for (let column = 0; column < visibleWidth; column += 1) {
          const state = frame.grid[0]?.[viewX + column - frame.grid_origin.x];
          if (state !== undefined) paintCell(state, column, 0);
        }
      }
    } else {
      for (let row = 0; row < visibleHeight; row += 1) {
        for (let column = 0; column < visibleWidth; column += 1) {
          const state = frame.grid[viewY + row - frame.grid_origin.y]?.[viewX + column - frame.grid_origin.x];
          if (state !== undefined) paintCell(state, column, row);
        }
      }
    }
  }, [frame, history, timelineRows, timelineTop, visibleHeight, visibleWidth, viewX, viewY, cellSize]);

  function paintAt(event: PointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    const column = Math.floor(((event.clientX - bounds.left) / bounds.width) * visibleWidth);
    const row = Math.floor(((event.clientY - bounds.top) / bounds.height) * (frame.one_dimensional ? timelineRows : visibleHeight));
    if (column < 0 || column >= visibleWidth || row < 0) return;
    if (frame.one_dimensional) {
      if (timelineTop + row !== history.length - 1) return;
      const worldX = viewX + column;
      const key = `0:${worldX}`;
      if (key === lastPaintRef.current) return;
      lastPaintRef.current = key;
      onPaint(worldX, 0, brush);
    } else {
      if (row >= visibleHeight) return;
      const worldX = viewX + column;
      const worldY = viewY + row;
      const key = `${worldY}:${worldX}`;
      if (key === lastPaintRef.current) return;
      lastPaintRef.current = key;
      onPaint(worldX, worldY, brush);
    }
  }

  function beginPaint(event: PointerEvent<HTMLCanvasElement>) {
    event.preventDefault();
    paintingRef.current = true;
    lastPaintRef.current = "";
    onEditStart();
    event.currentTarget.setPointerCapture(event.pointerId);
    paintAt(event);
  }

  function movePaint(event: PointerEvent<HTMLCanvasElement>) {
    if (paintingRef.current && event.buttons > 0) paintAt(event);
  }

  function finishPaint() {
    if (!paintingRef.current) return;
    paintingRef.current = false;
    lastPaintRef.current = "";
    onEditEnd();
  }

  function moveCamera(direction: "up" | "down" | "left" | "right", distance = 1) {
    if (frame.one_dimensional && (direction === "up" || direction === "down")) {
      const directionDelta = direction === "up" ? -distance : distance;
      setTimelineTop((previous) => Math.max(0, Math.min(maxTimelineTop, previous + directionDelta)));
      return;
    }
    const current = cameraRef.current;
    const nextX = current.x + (direction === "left" ? -distance : direction === "right" ? distance : 0);
    const nextY = current.y + (direction === "up" ? -distance : direction === "down" ? distance : 0);
    const next = {
      x: frame.infinite ? nextX : Math.max(0, Math.min(worldWidth - visibleWidth, nextX)),
      y: frame.one_dimensional
        ? 0
        : frame.infinite ? nextY : Math.max(0, Math.min(worldHeight - visibleHeight, nextY)),
    };
    cameraRef.current = next;
    setCamera(next);
  }

  function stopPan() {
    panDirectionRef.current = null;
    panElapsedRef.current = 0;
    if (panFrameRef.current !== null) {
      window.cancelAnimationFrame(panFrameRef.current);
      panFrameRef.current = null;
    }
  }

  function startPan(direction: "up" | "down" | "left" | "right") {
    stopPan();
    panDirectionRef.current = direction;
    panStartedAtRef.current = performance.now();
    panLastStepRef.current = panStartedAtRef.current;
    // A short press still pans one cell; holding continues into accelerated motion.
    moveCamera(direction, 1);
    const tick = (now: number) => {
      const activeDirection = panDirectionRef.current;
      if (!activeDirection) return;
      const interval = Math.max(24, 150 - (now - panStartedAtRef.current) * 0.09);
      panElapsedRef.current += now - panLastStepRef.current;
      panLastStepRef.current = now;
      if (panElapsedRef.current >= interval) {
        const steps = Math.min(5, Math.floor(panElapsedRef.current / interval));
        panElapsedRef.current -= steps * interval;
        moveCamera(activeDirection, steps);
      }
      panFrameRef.current = window.requestAnimationFrame(tick);
    };
    panFrameRef.current = window.requestAnimationFrame(tick);
  }

  function setOrigin(axis: "x" | "y", value: string) {
    setOriginInputs((previous) => ({ ...previous, [axis]: value }));
    if (!/^-?\d+$/.test(value)) return;
    const origin = Number(value);
    if (!Number.isSafeInteger(origin) || (axis === "y" && frame.one_dimensional && origin < 0) || (!frame.infinite && !frame.one_dimensional && origin < 0)) return;
    if (frame.one_dimensional && axis === "y") {
      const rowIndex = history.findIndex((row) => row.generation >= origin);
      setTimelineTop(Math.max(0, Math.min(maxTimelineTop, rowIndex < 0 ? maxTimelineTop : rowIndex)));
      return;
    }
    const maximum = axis === "x" ? worldWidth - visibleWidth : worldHeight - visibleHeight;
    setCamera((previous) => ({
      ...previous,
      [axis]: frame.infinite ? origin : Math.max(0, Math.min(origin, maximum)),
    }));
  }

  function finishOriginEdit(axis: "x" | "y") {
    setOriginInputs((previous) => ({ ...previous, [axis]: String(cameraRef.current[axis]) }));
  }

  function changeZoom(factor: number) {
    onZoomChange(Math.max(0.5, Math.min(6, Math.round(zoom * factor * 100) / 100)));
  }

  return (
    <div className="board-viewport" ref={viewportRef}>
      <div className="canvas-scroll">
        <canvas
          ref={canvasRef}
          className="automata-canvas"
          aria-label={frame.one_dimensional ? "Editable elementary automaton viewport" : "Editable automaton viewport"}
          onPointerDown={beginPaint}
          onPointerMove={movePaint}
          onPointerUp={finishPaint}
          onPointerCancel={finishPaint}
          onLostPointerCapture={finishPaint}
        />
      </div>
      <div className="viewport-controls" aria-label="Generation view navigation">
        <div className="pan-pad" aria-label="Pan grid">
          <span className="pan-empty" />
          <button className="view-control" type="button" aria-label="Pan up" title="Pan up through generations" onPointerDown={(event) => { event.preventDefault(); startPan("up"); }} onPointerUp={stopPan} onPointerCancel={stopPan} onPointerLeave={stopPan} onClick={(event) => { if (event.detail === 0) moveCamera("up"); }} disabled={frame.one_dimensional ? timelineTop <= 0 : (!frame.infinite && viewY <= 0)}>
            <ArrowIcon direction="up" />
          </button>
          <span className="pan-empty" />
          <button className="view-control" type="button" aria-label="Pan left" title="Pan left" onPointerDown={(event) => { event.preventDefault(); startPan("left"); }} onPointerUp={stopPan} onPointerCancel={stopPan} onPointerLeave={stopPan} onClick={(event) => { if (event.detail === 0) moveCamera("left"); }} disabled={!frame.infinite && viewX <= 0}>
            <ArrowIcon direction="left" />
          </button>
          <span className="pan-center" aria-hidden="true" />
          <button className="view-control" type="button" aria-label="Pan right" title="Pan right" onPointerDown={(event) => { event.preventDefault(); startPan("right"); }} onPointerUp={stopPan} onPointerCancel={stopPan} onPointerLeave={stopPan} onClick={(event) => { if (event.detail === 0) moveCamera("right"); }} disabled={!frame.infinite && viewX >= worldWidth - visibleWidth}>
            <ArrowIcon direction="right" />
          </button>
          <span className="pan-empty" />
          <button className="view-control" type="button" aria-label="Pan down" title="Pan down through generations" onPointerDown={(event) => { event.preventDefault(); startPan("down"); }} onPointerUp={stopPan} onPointerCancel={stopPan} onPointerLeave={stopPan} onClick={(event) => { if (event.detail === 0) moveCamera("down"); }} disabled={frame.one_dimensional ? timelineTop >= maxTimelineTop : (!frame.infinite && viewY >= worldHeight - visibleHeight)}>
            <ArrowIcon direction="down" />
          </button>
          <span className="pan-empty" />
        </div>
        <div className="origin-controls" aria-label="Camera origin">
          <label>X <input type="text" inputMode="text" pattern="-?[0-9]*" value={originInputs.x} onChange={(event) => setOrigin("x", event.target.value)} onBlur={() => finishOriginEdit("x")} /></label>
          <label>Y <input type="text" inputMode="text" pattern="-?[0-9]*" value={originInputs.y} disabled={frame.one_dimensional} onChange={(event) => setOrigin("y", event.target.value)} onBlur={() => finishOriginEdit("y")} /></label>
        </div>
        <div className="zoom-pad" aria-label="Zoom generation view">
          <button className="view-control zoom-control" type="button" aria-label="Zoom out" title="Zoom out" onClick={() => changeZoom(0.8)} disabled={zoom <= 0.5}>
            <MagnifierIcon sign="minus" />
          </button>
          <button className="view-control zoom-control" type="button" aria-label="Zoom in" title="Zoom in" onClick={() => changeZoom(1.25)} disabled={zoom >= 6}>
            <MagnifierIcon sign="plus" />
          </button>
        </div>
      </div>
    </div>
  );
}

function ArrowIcon({ direction }: { direction: "up" | "down" | "left" | "right" }) {
  const rotations = { up: 0, right: 90, down: 180, left: 270 };
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" width="17" height="17" style={{ transform: `rotate(${rotations[direction]}deg)` }}>
      <path d="M10 16V4m0 0L5.5 8.5M10 4l4.5 4.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7" />
    </svg>
  );
}

function MagnifierIcon({ sign }: { sign: "plus" | "minus" }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" width="17" height="17">
      <circle cx="8.4" cy="8.4" r="5.2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="m12.3 12.3 4.2 4.2M5.8 8.4h5.2" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />
      {sign === "plus" && <path d="M8.4 5.8V11" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />}
    </svg>
  );
}
