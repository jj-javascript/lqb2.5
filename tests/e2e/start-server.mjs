// Playwright webServer entry point (local mode): inject fixture books +
// covers, build the site with the test config override, then serve
// _site_test/ statically. Playwright launches its webServer BEFORE global
// setup, which is why the build lives here rather than in global-setup.ts.
// Fixture cleanup happens in global-teardown.ts after the run.
import { execSync } from 'node:child_process';
import { copyFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));

for (const file of readdirSync(join(ROOT, 'tests', 'fixtures', 'books'))) {
  copyFileSync(join(ROOT, 'tests', 'fixtures', 'books', file), join(ROOT, '_books', file));
}
for (const file of readdirSync(join(ROOT, 'tests', 'fixtures', 'covers'))) {
  copyFileSync(
    join(ROOT, 'tests', 'fixtures', 'covers', file),
    join(ROOT, 'assets', 'images', 'books', file)
  );
}

execSync(
  'bundle exec jekyll build -d _site_test --config _config.yml,tests/fixtures/_config_test.yml',
  { cwd: ROOT, stdio: 'inherit' }
);

await import('./serve.mjs');
