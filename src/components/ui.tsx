import { cn } from "@/lib/utils";

/*
 * Shared primitives (see DESIGN.md). Kept deliberately small: Button and
 * Chip are the only elements that repeat across views.
 */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger-ghost";
type ButtonSize = "sm" | "md";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-accent-fg font-medium hover:opacity-90 active:scale-[0.97]",
  secondary:
    "border border-border-strong bg-surface text-fg font-medium hover:border-fg-4 active:scale-[0.97]",
  ghost: "text-fg-2 font-medium hover:bg-surface-2 hover:text-fg",
  "danger-ghost":
    "text-fg-2 font-medium hover:bg-red-500/10 hover:text-red-600 dark:hover:text-red-400",
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs rounded-md",
  md: "h-9 px-4 text-sm rounded-md",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  return (
    <button
      className={cn(
        "inline-flex cursor-pointer items-center justify-center gap-1.5 touch-manipulation",
        "transition-[transform,background-color,border-color,opacity,color] duration-150 ease-out-expo",
        "focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas focus-visible:outline-none",
        "disabled:pointer-events-none disabled:opacity-50 disabled:cursor-not-allowed",
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    />
  );
}

type ChipTone = "neutral" | "accent" | "success" | "warn" | "danger";

const CHIP_TONES: Record<ChipTone, string> = {
  neutral: "border-border bg-surface-2/60 text-fg-2",
  accent: "border-accent/25 bg-accent-soft text-accent",
  success: "border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  warn: "border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  danger: "border-red-500/25 bg-red-500/10 text-red-700 dark:text-red-400",
};

export function Chip({
  tone = "neutral",
  dot = false,
  pulse = false,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: ChipTone;
  dot?: boolean;
  pulse?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        CHIP_TONES[tone],
        className,
      )}
      {...props}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={cn(
            "h-1.5 w-1.5 rounded-full bg-current",
            pulse && "animate-pulse",
          )}
        />
      )}
      {children}
    </span>
  );
}

export function Logomark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={className}>
      <rect x="1" y="1" width="30" height="30" rx="7" className="fill-fg" />
      <path
        d="M9 11h14M9 16h14M9 21h8"
        stroke="var(--surface)"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <circle cx="23.5" cy="21" r="2.5" className="fill-accent" />
    </svg>
  );
}
