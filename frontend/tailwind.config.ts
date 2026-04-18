import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        felt: "#0b3d2e",
        feltLight: "#12553f",
        chip: { white: "#f5f5f5", red: "#e53935", blue: "#1976d2", gold: "#fbc02d" },
      },
      fontFamily: { mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
} satisfies Config;
