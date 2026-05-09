import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,jsx,ts,tsx}", "./components/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        waxwing: {
          teal: "#0F5963",
          sand: "#E8C39E",
          paper: "#FAF8F4"
        }
      }
    }
  },
  plugins: []
};

export default config;
