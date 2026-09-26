"use client";

import type { Prediction } from "@/lib/api/client";

/** The 8 highest-attention tiles; clicking one zooms the viewer to it. */
export function TopTiles({
  tiles,
  onSelect,
}: {
  tiles: Prediction["top_tiles"];
  onSelect: (x: number, y: number) => void;
}) {
  if (!tiles.length) return <p className="text-sm text-muted-foreground">No tiles.</p>;
  return (
    <ul className="grid grid-cols-4 gap-2">
      {tiles.map((t) => (
        <li key={t.rank}>
          <button
            type="button"
            onClick={() => onSelect(t.x, t.y)}
            className="group relative block aspect-square w-full overflow-hidden rounded-md border transition hover:ring-2 hover:ring-primary focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Tile ${t.rank + 1}: attention rank ${t.rank + 1}, at x ${t.x}, y ${t.y}. Show in viewer`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- authenticated API image */}
            <img src={t.url} alt="" loading="lazy" className="size-full object-cover" />
            <span className="absolute left-1 top-1 rounded bg-black/70 px-1.5 text-[10px] font-semibold text-white">
              #{t.rank + 1}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
