import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig(() => ({
  server: {
    host: "::",
    port: 5174,
    proxy: {
      "/benchmarks": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
      "/architecture-profiles": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
      "/evaluate": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
      "/evaluate-v2": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
    },
  },
  plugins: [react()],
  build: {
    // The WebGL architecture canvas and the report charts are independent,
    // heavyweight surfaces. Keep them out of the application chunk so a
    // change to either dependency family does not invalidate all client code.
    // Three.js itself is intentionally close to Vite's default 500 kB limit;
    // 750 kB is our reviewed ceiling for a single vendor chunk.
    chunkSizeWarningLimit: 750,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("/node_modules/")) return undefined;
          if (id.includes("/node_modules/three/")) return "vendor-three";
          if (
            id.includes("/node_modules/@react-three/") ||
            id.includes("/node_modules/three-stdlib/") ||
            id.includes("/node_modules/postprocessing/")
          ) {
            return "vendor-react-three";
          }
          if (
            id.includes("/node_modules/recharts/") ||
            id.includes("/node_modules/d3-") ||
            id.includes("/node_modules/victory-vendor/")
          ) {
            return "vendor-charts";
          }
          if (
            id.includes("/node_modules/@radix-ui/") ||
            id.includes("/node_modules/lucide-react/") ||
            id.includes("/node_modules/react-resizable-panels/") ||
            id.includes("/node_modules/sonner/")
          ) {
            return "vendor-ui";
          }
          // React and small, shared runtime dependencies stay together. Some
          // of those packages reference React while React DOM references the
          // scheduler; separating them creates a Rollup chunk cycle.
          return "vendor-core";
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
