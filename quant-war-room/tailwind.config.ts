import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        "war-bg"     : "#0a0e1a",
        "war-panel"  : "#111827",
        "war-border" : "#1f2937",
        "war-accent" : "#00d4ff",
        "war-green"  : "#00ff88",
        "war-red"    : "#ff3b5c",
        "war-yellow" : "#ffd700",
        "war-purple" : "#a855f7",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
