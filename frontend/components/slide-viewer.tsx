"use client";

/**
 * Whole-slide viewer: OpenSeadragon on the server's DeepZoom tiles (the slide itself never
 * leaves the server), with the attention heatmap as a second, aligned image layer.
 */
import { Expand, Home, Layers, Minus, Plus } from "lucide-react";
import type OpenSeadragonType from "openseadragon";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type OSD = typeof OpenSeadragonType;
type Viewer = OpenSeadragonType.Viewer;
type TiledImage = OpenSeadragonType.TiledImage;

export interface SlideViewerHandle {
  /** Pan/zoom to a level-0 rectangle and outline it. */
  focus(x: number, y: number, size: number): void;
}

interface Props {
  dziUrl: string;
  heatmapUrl?: string | null;
  mpp?: number | null;
  label: string;
}

const NICE = [1, 2, 5];

function scaleBar(umPerPx: number, targetPx = 110): { px: number; label: string } {
  const target = umPerPx * targetPx;
  const exp = Math.floor(Math.log10(target));
  let best: { px: number; value: number } | null = null;
  for (const e of [exp - 1, exp, exp + 1]) {
    for (const n of NICE) {
      const value = n * 10 ** e; // round lengths only: 1, 2, 5 x 10^k
      const px = value / umPerPx;
      if (px <= targetPx * 1.6 && (!best || Math.abs(px - targetPx) < Math.abs(best.px - targetPx)))
        best = { px, value };
    }
  }
  best ??= { px: targetPx, value: target };
  const label = best.value >= 1000 ? `${+(best.value / 1000).toFixed(2)} mm` : `${+best.value.toFixed(1)} µm`;
  return { px: best.px, label };
}

export const SlideViewer = forwardRef<SlideViewerHandle, Props>(function SlideViewer(
  { dziUrl, heatmapUrl, mpp, label },
  ref,
) {
  const container = useRef<HTMLDivElement>(null);
  const viewer = useRef<Viewer | null>(null);
  const osd = useRef<OSD | null>(null);
  const heat = useRef<TiledImage | null>(null);
  const highlight = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [showHeat, setShowHeat] = useState(true);
  const [opacity, setOpacity] = useState(0.5);
  const [bar, setBar] = useState<{ px: number; label: string } | null>(null);

  const updateBar = useCallback(() => {
    const v = viewer.current;
    if (!v || !mpp || !v.world.getItemCount()) return;
    const zoom = v.viewport.viewportToImageZoom(v.viewport.getZoom(true)); // screen px per image px
    if (zoom > 0) setBar(scaleBar(mpp / zoom));
  }, [mpp]);

  useEffect(() => {
    let destroyed = false;
    setReady(false); // a new viewer must re-add the heatmap layer once it has opened
    setFailed(false);
    void import("openseadragon").then((mod) => {
      if (destroyed || !container.current) return;
      const OpenSeadragon = (mod.default ?? mod) as OSD;
      osd.current = OpenSeadragon;
      const v = OpenSeadragon({
        element: container.current,
        tileSources: dziUrl,
        showNavigationControl: false,
        showNavigator: true,
        navigatorPosition: "BOTTOM_RIGHT",
        navigatorSizeRatio: 0.18,
        maxZoomPixelRatio: 2,
        visibilityRatio: 0.5,
        minZoomImageRatio: 0.5,
        animationTime: 0.6,
        gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true },
        crossOriginPolicy: false,
        loadTilesWithAjax: false,
      });
      viewer.current = v;
      v.addHandler("open", () => {
        setReady(true);
        updateBar();
      });
      v.addHandler("open-failed", () => setFailed(true));
      v.addHandler("animation", updateBar);
      v.addHandler("resize", updateBar);
    });
    return () => {
      destroyed = true;
      viewer.current?.destroy();
      viewer.current = null;
      heat.current = null;
      highlight.current = null;
    };
  }, [dziUrl, updateBar]);

  // heatmap layer: stretched over the whole slide (x=0, width=1 in viewport coordinates)
  useEffect(() => {
    const v = viewer.current;
    if (!ready || !v || !heatmapUrl) return;
    if (heat.current) {
      heat.current.setOpacity(showHeat ? opacity : 0);
      return;
    }
    v.addTiledImage({
      tileSource: { type: "image", url: heatmapUrl, buildPyramid: false },
      x: 0,
      y: 0,
      width: 1,
      opacity: showHeat ? opacity : 0,
      success: (e: { item: TiledImage }) => {
        heat.current = e.item;
        e.item.setOpacity(showHeat ? opacity : 0);
      },
    } as unknown as Parameters<Viewer["addTiledImage"]>[0]);
  }, [ready, heatmapUrl, showHeat, opacity]);

  useImperativeHandle(ref, () => ({
    focus(x, y, size) {
      const v = viewer.current;
      const O = osd.current;
      if (!v || !O) return;
      const pad = size * 1.5;
      const target = v.viewport.imageToViewportRectangle(x - pad, y - pad, size + 2 * pad, size + 2 * pad);
      v.viewport.fitBounds(target);
      if (highlight.current) v.removeOverlay(highlight.current);
      const el = document.createElement("div");
      el.style.outline = "3px solid #facc15";
      el.style.boxShadow = "0 0 0 2px rgba(0,0,0,.6)";
      el.style.borderRadius = "2px";
      el.setAttribute("aria-hidden", "true");
      highlight.current = el;
      v.addOverlay(el, v.viewport.imageToViewportRectangle(x, y, size, size));
    },
  }));

  const zoomBy = (f: number) => viewer.current?.viewport.zoomBy(f).applyConstraints();

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-3 py-2">
        <div className="flex items-center gap-1" role="toolbar" aria-label="Viewer controls">
          {[
            { icon: Plus, label: "Zoom in", onClick: () => zoomBy(1.5) },
            { icon: Minus, label: "Zoom out", onClick: () => zoomBy(1 / 1.5) },
            { icon: Home, label: "Fit whole slide", onClick: () => viewer.current?.viewport.goHome() },
            {
              icon: Expand,
              label: "Full screen",
              onClick: () => viewer.current?.setFullScreen(!viewer.current.isFullPage()),
            },
          ].map((b) => (
            <Tooltip key={b.label}>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  onClick={b.onClick}
                  aria-label={b.label}
                  disabled={!ready}
                >
                  <b.icon aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{b.label}</TooltipContent>
            </Tooltip>
          ))}
        </div>
        {heatmapUrl && (
          <div className="flex flex-1 flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Switch id="heatmap" checked={showHeat} onCheckedChange={setShowHeat} disabled={!ready} />
              <Label htmlFor="heatmap" className="flex items-center gap-1.5">
                <Layers className="size-4" aria-hidden /> Attention heatmap
              </Label>
            </div>
            <div className="flex min-w-[160px] max-w-[220px] flex-1 items-center gap-2">
              <span className="text-xs text-muted-foreground" id="opacity-label">
                Opacity
              </span>
              <Slider
                value={[opacity]}
                min={0.1}
                max={1}
                step={0.05}
                onValueChange={(v) => setOpacity(v[0] ?? 0.5)}
                disabled={!showHeat || !ready}
                thumbLabel="Heatmap opacity"
                aria-labelledby="opacity-label"
              />
            </div>
          </div>
        )}
      </div>
      <div className="relative min-h-[420px] flex-1 bg-neutral-100 dark:bg-neutral-900">
        <div
          ref={container}
          className="osd-viewer absolute inset-0"
          role="img"
          aria-label={`Zoomable slide viewer: ${label}. Use the mouse wheel or the + and - keys to zoom, arrow keys to pan.`}
          tabIndex={0}
        />
        {!ready && !failed && (
          <p className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
            Loading slide...
          </p>
        )}
        {failed && (
          <p className="absolute inset-0 flex items-center justify-center text-sm text-destructive">
            The slide could not be opened.
          </p>
        )}
        {bar && (
          <div
            className="pointer-events-none absolute bottom-3 left-3 rounded bg-black/65 px-2 py-1 text-xs font-medium text-white"
            aria-hidden
          >
            <div className="mb-0.5 h-1.5 border-x-2 border-b-2 border-white" style={{ width: bar.px }} />
            {bar.label}
          </div>
        )}
        {heatmapUrl && showHeat && ready && (
          <div
            className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded bg-black/65 px-2 py-1 text-[11px] text-white"
            aria-hidden
          >
            low
            <span
              className="h-2 w-20 rounded"
              style={{ background: "linear-gradient(90deg,#30123b,#28bbec,#a4fc3c,#fb8022,#7a0403)" }}
            />
            high attention
          </div>
        )}
      </div>
    </div>
  );
});
