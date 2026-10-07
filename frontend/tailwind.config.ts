import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#d9e6ff",
          200: "#bcd2ff",
          300: "#8fb4ff",
          400: "#5c8cff",
          500: "#3366ff",
          600: "#1f49e6",
          700: "#1a3bb4",
          800: "#1a328f",
          900: "#1b2e71",
        },
      },
    },
  },
  plugins: [],
};

export default config;
