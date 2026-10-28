import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

// Next.js's recommended rules (React, hooks, accessibility, Core Web Vitals) plus typescript-eslint's.
// Unused code is an error, not a warning: CI fails on it.
const config = [
  ...nextVitals,
  ...nextTypescript,
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-unused-expressions": "error",
    },
  },
  { ignores: [".next/", "node_modules/", "next-env.d.ts"] },
];

export default config;
