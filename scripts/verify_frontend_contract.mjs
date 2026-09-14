// Run the original FE's zod schemas against BE TestClient responses without editing FE.
import { mkdtempSync, copyFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

if (process.argv.length !== 4) {
  console.error("Usage: node scripts/verify_frontend_contract.mjs FE_PATH CONTRACT_JSON");
  process.exit(2);
}
const frontend = resolve(process.argv[2]);
const contract = resolve(process.argv[3]);
const temporary = mkdtempSync(join(tmpdir(), "ihaero-contract-"));
copyFileSync(fileURLToPath(new URL("../tests/frontend/contract.test.ts", import.meta.url)), join(temporary, "contract.test.ts"));
writeFileSync(join(temporary, "vitest.config.mts"), `export default ${JSON.stringify({
  resolve: { alias: { "@": frontend, zod: join(frontend, "node_modules/zod/index.js") } },
  test: { environment: "node", include: ["contract.test.ts"] },
})};\n`);
const result = spawnSync(process.execPath, [join(frontend, "node_modules/vitest/vitest.mjs"), "run", "--config", join(temporary, "vitest.config.mts")], {
  cwd: temporary, stdio: "inherit", env: { ...process.env, STUDIO_CONTRACT_FILE: contract },
});
process.exit(result.status ?? 1);
