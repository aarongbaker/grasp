import { spawn } from 'node:child_process';

const args = process.argv.slice(2).map((arg) => (arg.startsWith('frontend/') ? arg.slice('frontend/'.length) : arg));
const vitestBin = new URL('../node_modules/vitest/vitest.mjs', import.meta.url);

const child = spawn(process.execPath, [vitestBin.pathname, ...args], {
  stdio: 'inherit',
  env: process.env,
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
