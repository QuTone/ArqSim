import { pathToFileURL } from "node:url";

import { build } from "esbuild";

const outputPath = "/tmp/arqsim-report-adapter-test.mjs";

await build({
  entryPoints: ["src/services/reportAdapter.test.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: outputPath,
});

await import(pathToFileURL(outputPath).href);
