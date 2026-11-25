// Shared paths for fixture injection (global setup) and cleanup (teardown).
import { join } from 'node:path';

export const ROOT = join(__dirname, '..', '..');
export const FIXTURE_BOOKS_DIR = join(ROOT, 'tests', 'fixtures', 'books');
export const FIXTURE_COVERS_DIR = join(ROOT, 'tests', 'fixtures', 'covers');
export const BOOKS_DIR = join(ROOT, '_books');
export const COVERS_DIR = join(ROOT, 'assets', 'images', 'books');
