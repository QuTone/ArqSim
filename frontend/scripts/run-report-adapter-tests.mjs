import { pathToFileURL } from "node:url";

import { build } from "esbuild";

const outputPath = "/tmp/arqsim-report-adapter-test.mjs";

await build({
  stdin: {
    contents: [
      'import "./src/services/reportAdapter.test.ts";',
      'import "./src/services/profile23Acceptance.test.ts";',
    ].join("\n"),
    resolveDir: process.cwd(),
    sourcefile: "report-adapter-tests.ts",
  },
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: outputPath,
});

await import(pathToFileURL(outputPath).href);
