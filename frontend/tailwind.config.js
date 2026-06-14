/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0a0b12",
        brand: { DEFAULT: "#6366f1", fg: "#ffffff" },
        pos: "#34d399", // emerald (neon on dark)
        neu: "#94a3b8",
        neg: "#fb7185", // rose
      },
      boxShadow: {
        glow: "0 14px 40px -12px rgba(99,102,241,.65)",
        "glow-sm": "0 8px 24px -10px rgba(99,102,241,.55)",
        card: "0 16px 50px -24px rgba(0,0,0,.8)",
      },
      backgroundImage: {
        "brand-grad": "linear-gradient(135deg,#818cf8 0%,#a78bfa 45%,#22d3ee 100%)",
      },
      keyframes: {
        drift: {
          "0%,100%": { transform: "translate(0,0) scale(1)" },
          "50%": { transform: "translate(36px,-44px) scale(1.18)" },
        },
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        drift: "drift 16s ease-in-out infinite",
        "drift-slow": "drift 22s ease-in-out infinite",
        "fade-up": "fade-up .35s ease-out both",
      },
    },
  },
  plugins: [],
};
