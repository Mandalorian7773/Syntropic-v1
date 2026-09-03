/**
 * Start frontend/mock/server.py for a test file, on any machine.
 *
 * The suites used to spawn `python3` directly, which is right on Linux and CI
 * and wrong on Windows: there `python3` resolves to the Microsoft Store stub,
 * a shim that prints an advertisement and exits without running anything. The
 * spawn "succeeds", nothing ever listens, and every suite fails twenty seconds
 * later with "mock server did not start" -- a message that points at the mock
 * rather than at the interpreter.
 *
 * So the interpreter is probed rather than assumed: the first candidate that
 * actually reports a Python 3 version is the one used.
 */
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';

const CANDIDATES = ['python3', 'python', 'py'];

let cached: string | null = null;

export function pythonBin(): string {
  if (cached) return cached;
  for (const bin of CANDIDATES) {
    const probe = spawnSync(bin, ['-c', 'import sys; print(sys.version_info[0])'],
                            { encoding: 'utf8', timeout: 10_000 });
    if (probe.status === 0 && probe.stdout.trim() === '3') {
      cached = bin;
      return bin;
    }
  }
  throw new Error(
    `no working Python 3 found (tried ${CANDIDATES.join(', ')}). ` +
    'The mock server needs one; on Windows the "python3" on PATH is often ' +
    'the Microsoft Store stub, which is not an interpreter.',
  );
}

/** Spawn the mock and resolve once it answers /api/health. */
export async function startMock(port: number, cwd?: string): Promise<ChildProcess> {
  const proc = spawn(pythonBin(),
                     ['mock/server.py', '--port', String(port), '--fast'],
                     { stdio: 'ignore', cwd });
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${port}/api/health`)).ok) return proc;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 100));
  }
  proc.kill();
  throw new Error(`mock server did not start on port ${port} using ${pythonBin()}`);
}
