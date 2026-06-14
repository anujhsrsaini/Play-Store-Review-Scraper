import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/50 disabled:opacity-50 disabled:pointer-events-none active:scale-[.98]",
  {
    variants: {
      variant: {
        primary:
          "bg-brand-grad text-white shadow-glow-sm hover:shadow-glow hover:brightness-110",
        ghost: "border border-white/10 bg-white/[0.04] text-slate-200 hover:bg-white/[0.09]",
        outline: "border border-white/15 bg-transparent text-slate-200 hover:bg-white/[0.06]",
      },
      size: { md: "h-10 px-4 text-sm", sm: "h-8 px-3 text-[13px]", lg: "h-11 px-5 text-[15px]" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export function Button({ className, variant, size, loading, children, disabled, ...props }: ButtonProps) {
  return (
    <button
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("glass animate-fade-up", className)} {...props} />;
}

export function Badge({
  className,
  tone = "neutral",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: "neutral" | "positive" | "negative" }) {
  const tones = {
    neutral: "bg-white/10 text-slate-300 ring-1 ring-inset ring-white/10",
    positive: "bg-emerald-400/10 text-emerald-300 ring-1 ring-inset ring-emerald-400/20",
    negative: "bg-rose-400/10 text-rose-300 ring-1 ring-inset ring-rose-400/20",
  };
  return (
    <span
      className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium", tones[tone], className)}
      {...props}
    />
  );
}

const fieldBase =
  "w-full rounded-xl border border-white/10 bg-white/[0.04] text-slate-100 shadow-inner placeholder:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/40 focus-visible:border-indigo-400/40";

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(fieldBase, "resize-y px-3.5 py-2.5 text-sm", className)} {...props} />;
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(fieldBase, "h-12 px-4 text-sm", className)} {...props} />;
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin text-indigo-300", className)} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-white/10", className)} />;
}
