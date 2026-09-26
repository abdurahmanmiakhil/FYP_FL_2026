import { cn } from "@/lib/utils";

/** GleasonAI mark: a stylised biopsy core with an attention hot-spot. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={cn("size-8", className)} role="img" aria-label="GleasonAI">
      <rect width="48" height="48" rx="12" className="fill-primary" />
      <path
        d="M10 30c6-10 22-12 28-4"
        strokeWidth="5"
        strokeLinecap="round"
        fill="none"
        className="stroke-primary-foreground/90"
      />
      <circle cx="30" cy="21" r="4.5" className="fill-amber-300" />
      <circle cx="30" cy="21" r="2" className="fill-red-500" />
    </svg>
  );
}
