const { app, BrowserWindow, Menu, ipcMain, protocol, net, dialog, shell, clipboard } = require('electron');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const fs = require('node:fs');

protocol.registerSchemesAsPrivileged([{ scheme: 'atlas', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
app.setName('Atlas PDF');
app.setDesktopName('Atlas PDF.desktop');
const root = process.env.ATLAS_RESOURCE_DIR || path.resolve(__dirname, '..');
const option = (name) => {
  const index = process.argv.indexOf(name);
  return index < 0 ? undefined : process.argv[index + 1];
};
const dataDir = path.resolve(option('--data-dir') || process.env.ATLAS_DATA_DIR ||
  path.join(process.env.XDG_DATA_HOME || path.join(app.getPath('home'), '.local/share'), 'atlas-pdf'));
const webDir = path.join(root, 'build/web');
let window;
let scanning = null;
let quitting = false;
const children = new Set();
let books = new Map();
let categories = [];

function backend(args, onEvent) {
  const environment = { ...process.env, PYTHONUNBUFFERED: '1' };
  if (process.env.ATLAS_BACKEND_LIBRARY_PATH) environment.LD_LIBRARY_PATH = process.env.ATLAS_BACKEND_LIBRARY_PATH;
  if (process.env.ATLAS_BACKEND_PYTHONHOME) environment.PYTHONHOME = process.env.ATLAS_BACKEND_PYTHONHOME;
  if (process.env.ATLAS_BACKEND_PATH) environment.PATH = process.env.ATLAS_BACKEND_PATH;
  const child = spawn(process.env.ATLAS_PYTHON || '/usr/bin/python3',
    [path.join(root, 'backend/indexer.py'), '--data-dir', dataDir, ...args],
    { env: environment, stdio: ['ignore', 'pipe', 'pipe'] });
  children.add(child);
  let buffer = '';
  let error = '';
  const events = [];
  const parse = (line) => {
    if (!line.trim()) return;
    try {
      const event = JSON.parse(line);
      if (onEvent) onEvent(event);
      else events.push(event);
    } catch { error = 'Resposta inválida do indexador.'; }
  };
  child.stdout.setEncoding('utf8').on('data', (chunk) => {
    buffer += chunk;
    if (buffer.length > 32 * 1024 * 1024) { error = 'Resposta do indexador muito grande.'; child.kill(); return; }
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      parse(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
    }
  });
  child.stderr.setEncoding('utf8').on('data', (chunk) => { error = (error + chunk).slice(-6000); });
  const done = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code) => {
      children.delete(child);
      parse(buffer);
      if (code === 0 || code === 130) resolve(events);
      else reject(new Error(error.trim() || `O indexador encerrou com código ${code}.`));
    });
  });
  return { child, done };
}

async function catalog() {
  const [result, categoryResult] = await Promise.all([
    backend(['list']).done, backend(['categories']).done,
  ]);
  const data = result.find((event) => event.type === 'catalog');
  if (!data) throw new Error('Não foi possível carregar o catálogo.');
  categories = categoryResult[0].categories;
  books = new Map(data.books.map((book) => [book.id, book]));
  return {
    ...data, categories, subjects: categoryResult[0].subjects, scanning: Boolean(scanning),
    books: data.books.map((book) => ({
      ...book, thumbnail: book.thumbnail ? `atlas://app/cover/${book.id}` : '',
    })),
  };
}

function getBook(id) {
  if (typeof id !== 'string' || !books.has(id)) throw new Error('PDF não encontrado. Atualize a biblioteca.');
  return books.get(id);
}

function send(event) {
  if (window && !window.isDestroyed()) window.webContents.send('atlas:progress', event);
}

function handle(name, callback) {
  ipcMain.handle(`atlas:${name}`, (event, ...args) => {
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
        !event.senderFrame.url.startsWith('atlas://app/')) throw new Error('Origem inválida.');
    return callback(...args);
  });
}

function installHandlers() {
  handle('catalog', catalog);
  handle('scan', async (mode) => {
    if (scanning) throw new Error('Uma busca já está em andamento.');
    const modes = { defaults: [], home: ['--all-home'], computer: ['--computer'], force: ['--force'], folder: [] };
    if (!Object.hasOwn(modes, mode)) throw new Error('Modo de busca inválido.');
    let args = modes[mode];
    if (mode === 'folder') {
      const selection = await dialog.showOpenDialog(window, { title: 'Adicionar pasta à biblioteca', properties: ['openDirectory'] });
      if (selection.canceled) return { cancelled: true };
      if (scanning) throw new Error('Uma busca já está em andamento.');
      args = ['--root', selection.filePaths[0]];
    }
    let lastProgress = 0;
    let finalEvent;
    scanning = backend(mode === 'force' ? ['reclassify', '--force'] : ['scan', ...args], (event) => {
      if (event.type === 'progress' && scanning) {
        scanning.started = true;
        if (scanning.cancelRequested) scanning.child.kill('SIGTERM');
      }
      if (event.type === 'complete') { finalEvent = event; return; }
      if (event.type === 'progress' && Date.now() - lastProgress < 60) return;
      lastProgress = Date.now();
      send(event);
    });
    scanning.done.then(() => {
      scanning = null;
      send(finalEvent || { type: 'complete', cancelled: true });
    }, (error) => { scanning = null; send({ type: 'error', message: error.message }); });
    return { started: true };
  });
  handle('cancel', () => {
    if (scanning) {
      scanning.cancelRequested = true;
      if (scanning.started) scanning.child.kill('SIGTERM');
    }
    return true;
  });
  handle('category', async (id, category) => {
    getBook(id);
    if (!categories.includes(category)) throw new Error('Área desconhecida.');
    await backend(['set-category', '--id', id, '--category', category]).done;
    return catalog();
  });
  handle('open', async (id) => {
    const book = getBook(id);
    if (!path.isAbsolute(book.path) || path.extname(book.path).toLowerCase() !== '.pdf') throw new Error('Caminho de PDF inválido.');
    const info = await fs.promises.stat(book.path);
    if (!info.isFile()) throw new Error('O PDF não está disponível.');
    const error = await shell.openPath(book.path);
    if (error) throw new Error(error);
  });
  handle('reveal', (id) => shell.showItemInFolder(getBook(id).path));
  handle('copy', (id) => { clipboard.writeText(getBook(id).path); return true; });
  handle('export', async () => {
    const selection = await dialog.showSaveDialog(window, { title: 'Exportar catálogo', defaultPath: 'Atlas — catálogo.txt', filters: [{ name: 'Texto', extensions: ['txt'] }] });
    if (selection.canceled) return null;
    await backend(['export', '--output', selection.filePath]).done;
    return selection.filePath;
  });
  handle('logs', async () => {
    const directory = path.join(dataDir, 'logs');
    await fs.promises.mkdir(directory, { recursive: true });
    const error = await shell.openPath(directory);
    if (error) throw new Error(error);
  });
  handle('window', (action) => {
    if (action === 'minimize') window.minimize();
    else if (action === 'maximize') window.isMaximized() ? window.unmaximize() : window.maximize();
    else if (action === 'close') window.close();
  });
}

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  protocol.handle('atlas', async (request) => {
    const url = new URL(request.url);
    if (url.host !== 'app') return new Response('', { status: 404 });
    let target;
    if (url.pathname.startsWith('/cover/')) {
      const book = books.get(url.pathname.slice('/cover/'.length));
      if (!book?.thumbnail) return new Response('', { status: 404 });
      const covers = await fs.promises.realpath(path.join(dataDir, 'thumbnails')).catch(() => '');
      const real = await fs.promises.realpath(book.thumbnail).catch(() => '');
      if (!covers || !real.startsWith(covers + path.sep) || path.extname(real) !== '.png') return new Response('', { status: 404 });
      target = real;
    } else {
      target = path.resolve(webDir, '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname));
      if (!target.startsWith(webDir + path.sep)) return new Response('', { status: 403 });
    }
    return net.fetch(pathToFileURL(target).href);
  });
  window = new BrowserWindow({
    title: 'Atlas PDF', width: 1440, height: 960, minWidth: 850, minHeight: 640,
    backgroundColor: '#f6f7f4', icon: path.join(root, 'assets/atlas.png'),
    frame: false, show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: !option('--screenshot') },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event) => event.preventDefault());
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  window.webContents.session.setPermissionCheckHandler(() => false);
  window.webContents.on('will-attach-webview', (event) => event.preventDefault());
  installHandlers();
  window.once('ready-to-show', () => { if (option('--screenshot')) window.showInactive(); else window.show(); });
  await window.loadURL('atlas://app/');
  if (option('--screenshot')) {
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await window.webContents.executeJavaScript('Boolean(window.__ATLAS_READY__)')) break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (process.argv.includes('--smoke')) {
      const state = await window.webContents.executeJavaScript(`({ ready: Boolean(window.__ATLAS_READY__), cards: document.querySelectorAll('[data-book-card]').length, error: document.querySelector('[role="alert"]')?.textContent })`);
      if (!state.ready || state.error || (books.size && !state.cards)) throw new Error(JSON.stringify(state));
    }
    await new Promise((resolve) => setTimeout(resolve, 700));
    const capture = await window.webContents.capturePage();
    fs.writeFileSync(path.resolve(option('--screenshot')), capture.toPNG());
    app.quit();
  }
}).catch((error) => { console.error(error); app.exit(1); });

app.on('window-all-closed', () => app.quit());
app.on('before-quit', (event) => {
  if (quitting || !children.size) return;
  event.preventDefault();
  quitting = true;
  for (const child of children) child.kill('SIGTERM');
  const timer = setTimeout(() => {
    for (const child of children) child.kill('SIGKILL');
    app.exit(0);
  }, 8000);
  Promise.all([...children].map((child) => new Promise((resolve) => child.once('close', resolve))))
    .then(() => { clearTimeout(timer); app.quit(); });
});
