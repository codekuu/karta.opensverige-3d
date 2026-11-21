import { cloudflareTest, readD1Migrations } from '@cloudflare/vitest-plugin';
import path from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig(async () => {
  const migrations = await readD1Migrations(path.join(import.meta.dirname, 'migrations'));
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: './wrangler.jsonc' },
        miniflare: {
          bindings: { TEST_MIGRATIONS: migrations, PUBLIC_CACHE_SECONDS: '0' },
        },
      }),
    ],
    test: {
      setupFiles: ['./test/setup.js'],
    },
  };
});
