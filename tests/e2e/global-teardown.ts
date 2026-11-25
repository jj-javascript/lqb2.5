// Local-mode global teardown: remove the fixture books/covers injected by
// global setup so the working tree is clean after the run. (The fixture
// names are also gitignored as a belt-and-suspenders against interrupted
// runs.) _site_test/ is kept for debugging; it is gitignored too.
import { readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import {
  BOOKS_DIR,
  COVERS_DIR,
  FIXTURE_BOOKS_DIR,
  FIXTURE_COVERS_DIR,
} from './fixture-paths';

export default async function globalTeardown(): Promise<void> {
  if (process.env.E2E_BASE_URL) return;
  for (const file of readdirSync(FIXTURE_BOOKS_DIR)) {
    rmSync(join(BOOKS_DIR, file), { force: true });
  }
  for (const file of readdirSync(FIXTURE_COVERS_DIR)) {
    rmSync(join(COVERS_DIR, file), { force: true });
  }
}
