const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

// Chromium's SUID helper cannot launch a zygote from a path containing spaces.
// Use a private temporary runtime, also used by the single-file launcher.
function prepareRuntime(destination) {
  const runtime = destination || fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-runtime-'));
  fs.cpSync(path.resolve(__dirname, '../node_modules/electron/dist'), runtime, { recursive: true, dereference: true });
  const sandbox = path.join(runtime, 'chrome-sandbox');
  fs.rmSync(sandbox, { force: true });
  for (const helper of ['/opt/google/chrome/chrome-sandbox', '/usr/lib/chromium/chrome-sandbox', '/usr/lib/chromium-browser/chrome-sandbox']) {
    try {
      const info = fs.statSync(helper);
      if (info.isFile() && info.uid === 0 && (info.mode & 0o4000) && (info.mode & 0o111)) {
        fs.symlinkSync(helper, sandbox);
        break;
      }
    } catch {}
  }
  return runtime;
}

module.exports = { prepareRuntime };
if (require.main === module) {
  const runtime = prepareRuntime();
  const child = spawn(path.join(runtime, 'electron'), [path.resolve(__dirname, '..'), ...process.argv.slice(2)], { stdio: 'inherit' });
  const clean = () => fs.rmSync(runtime, { recursive: true, force: true });
  process.on('SIGINT', () => child.kill('SIGINT'));
  process.on('SIGTERM', () => child.kill('SIGTERM'));
  child.once('error', (error) => { console.error(error.message); clean(); process.exitCode = 1; });
  child.once('exit', (code) => { clean(); process.exitCode = code ?? 1; });
}
