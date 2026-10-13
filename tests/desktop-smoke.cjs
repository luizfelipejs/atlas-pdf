// Exercise the real Electron bridge, SQLite catalog, protocol and desktop UI.
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { prepareRuntime } = require('../tools/electron-runtime.cjs');
const root = path.resolve(__dirname, '..');
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-desktop-test-'));
const data = path.join(fixture, 'data');
let application;

async function main() {
  // A synthetic, readable PDF and a filename resembling HTML exercise safe rendering.
  execFileSync('/usr/bin/python3', ['-c',
    'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from test_indexer import write_pdf; p=Path(sys.argv[2]); p.mkdir(); text="Calculus derivatives integrals limits functions continuous convergence series theorems proofs real analysis mathematics " * 4; write_pdf(p/"Cálculo — integração.PDF", text); write_pdf(p/"<img src=x onerror=alert(1)>.pdf", text)',
    __dirname, path.join(fixture, 'livros')]);
  execFileSync('/usr/bin/python3', [path.join(root, 'backend/indexer.py'), '--data-dir', data, 'scan', '--root', path.join(fixture, 'livros')]);
  const args = process.env.ATLAS_TEST_EXECUTABLE ? ['--data-dir', data, '--user-data-dir=' + path.join(fixture, 'settings')] : [root, '--data-dir', data, '--user-data-dir=' + path.join(fixture, 'settings')];
  application = await electron.launch({
    executablePath: process.env.ATLAS_TEST_EXECUTABLE || path.join(prepareRuntime(path.join(fixture, 'runtime')), 'electron'), args,
    env: { ...process.env, ATLAS_RESOURCE_DIR: process.env.ATLAS_TEST_EXECUTABLE ? '' : root },
    timeout: 90000,
  });
  const window = await application.firstWindow();
  const errors = [];
  window.on('pageerror', (error) => errors.push(error.message));
  await window.waitForFunction(() => window.__ATLAS_READY__);
  assert.equal(await window.locator('[data-book-card]').count(), 2);
  assert.equal(await window.evaluate(() => typeof window.require), 'undefined');
  assert.equal(await window.locator('.book-title img').count(), 0, 'PDF titles are text, not HTML');
  const book = (await window.evaluate(() => window.atlas.catalog())).books[0];
  const subjects = (await window.evaluate(() => window.atlas.catalog())).subjects;
  assert.ok(subjects.some((item) => item.name === 'Teoria dos grafos' && item.msc === '05C'));
  assert.ok(subjects.some((item) => item.name === 'Redes neurais em grafos'));
  assert.equal(await window.evaluate(async (url) => (await fetch(url)).status, book.thumbnail), 200, 'Cover protocol serves real thumbnail');
  assert.equal(await window.evaluate(async () => (await fetch('atlas://app/cover/not-a-book')).status), 404);
  await window.getByRole('textbox', { name: 'Pesquisar livros' }).fill('não existe este livro');
  assert.equal(await window.locator('[data-book-card]').count(), 0);
  await window.getByRole('button', { name: 'Limpar filtros' }).click();
  assert.equal(await window.locator('[data-book-card]').count(), 2);
  await window.getByRole('button', { name: 'Adicionar aos favoritos:', exact: false }).first().click();
  await window.getByRole('button', { name: /^Favoritos/ }).click();
  assert.equal(await window.locator('[data-book-card]').count(), 1);
  await window.locator('.cover-button').first().click();
  await window.getByRole('dialog').waitFor();
  assert.equal(await window.getByText('Tipo de documento', { exact: true }).count(), 1);
  assert.ok(await window.locator('.document-kind').textContent());
  assert.equal(await window.getByText('Tema sugerido pela leitura', { exact: true }).count(), 1);
  assert.ok(await window.locator('.topic-name').textContent());
  await window.getByText('Comparar evidências entre áreas', { exact: false }).click();
  assert.ok(await window.locator('.evidence-candidate').count() >= 1);
  await window.getByRole('button', { name: 'Copiar caminho' }).click();
  const copied = await application.evaluate(({ clipboard }) => clipboard.readText());
  assert.ok(copied.endsWith('.PDF') || copied.endsWith('.pdf'));
  await window.getByLabel('Ajustar área da matemática').selectOption('Topologia');
  await window.waitForFunction(() => document.querySelector('#category-edit')?.value === 'Topologia' && !document.querySelector('#category-edit')?.disabled);
  const changed = await window.evaluate(() => window.atlas.catalog());
  assert.ok(changed.books.some((item) => item.category === 'Topologia' && item.manual));
  await window.getByRole('button', { name: 'Fechar detalhes' }).click();
  await window.getByRole('dialog').waitFor({ state: 'detached' });
  await window.getByRole('button', { name: 'Recentes', exact: false }).click();
  assert.equal(await window.locator('[data-book-card]').count(), 1);
  await window.getByRole('button', { name: 'Áreas da matemática', exact: true }).click();
  assert.ok(await window.locator('.area-card').count() >= 1);
  assert.ok(await window.locator('.subject-family').count() >= 1);
  assert.ok(await window.locator('.msc-code').count() >= 1);
  await window.locator('.area-card').first().click();
  assert.ok(await window.locator('[data-book-card]').count() >= 1);
  await window.getByRole('button', { name: 'Exibir em lista' }).click();
  assert.ok(await window.locator('.list-card').count() >= 1);
  await window.getByRole('button', { name: 'Ativar tema escuro' }).click();
  assert.equal(await window.locator('html').getAttribute('data-theme'), 'dark');
  await window.reload();
  await window.waitForFunction(() => window.__ATLAS_READY__);
  assert.equal(await window.locator('html').getAttribute('data-theme'), 'dark');
  await window.getByRole('button', { name: /^Favoritos/ }).click();
  assert.equal(await window.locator('[data-book-card]').count(), 1, 'Favorites survive restarting the renderer');
  await window.getByRole('button', { name: 'Ativar tema claro' }).click();
  await window.getByRole('button', { name: 'Todos os livros', exact: false }).click();
  await window.getByRole('button', { name: 'Exibir em grade' }).click();
  await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(850, 640));
  const dimensions = await window.evaluate(() => ({ content: document.querySelector('.main-scroll').scrollWidth, client: document.querySelector('.main-scroll').clientWidth }));
  assert.ok(dimensions.content <= dimensions.client + 1, JSON.stringify(dimensions));
  await window.screenshot({ path: path.join(root, 'build/desktop-small-preview.png') });
  await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1440, 960));
  // Stub native dialogs and OS open operations in the main process to avoid opening
  // unrelated windows while exercising the actual IPC handlers and export backend.
  const output = path.join(fixture, 'exportado.txt');
  await application.evaluate(({ dialog, shell }, output) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: output });
    shell.openPath = async () => '';
    shell.showItemInFolder = () => {};
  }, output);
  await window.getByRole('button', { name: 'Exportar catálogo' }).click();
  await window.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('exportado'));
  assert.ok(fs.readFileSync(output, 'utf8').includes('Topologia'));
  await window.evaluate((id) => window.atlas.openBook(id), book.id);
  await assert.rejects(window.evaluate(() => window.atlas.openBook('invalid')));
  await window.getByRole('button', { name: 'Logs de busca' }).click();
  const childCount = await application.evaluate(() => process._getActiveHandles().filter((handle) => handle.constructor?.name === 'ChildProcess').length);
  assert.equal(childCount, 0);
  // A fresh, very small scan checks streaming progress, duplicate prevention and cancellation.
  await application.evaluate(({ dialog }, folder) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] }); }, path.join(fixture, 'livros'));
  const terminal = window.evaluate(() => new Promise(async (resolve, reject) => {
    const off = window.atlas.onScan((event) => {
      if (['complete', 'error'].includes(event.type)) { off(); resolve(event); }
    });
    try {
      await window.atlas.scan('folder');
      try { await window.atlas.scan('folder'); reject(new Error('Duplicate scan was accepted')); return; } catch {}
      await window.atlas.cancelScan();
    } catch (error) { off(); reject(error); }
  }));
  const result = await terminal;
  assert.equal(result.type, 'complete');
  await window.waitForFunction(() => !document.querySelector('.scan-progress'));
  const reclassification = await window.evaluate(() => new Promise(async (resolve, reject) => {
    const off = window.atlas.onScan((event) => {
      if (['complete', 'error'].includes(event.type)) { off(); resolve(event); }
    });
    try { await window.atlas.scan('force'); } catch (error) { off(); reject(error); }
  }));
  assert.equal(reclassification.type, 'complete');
  assert.equal(reclassification.manual_preserved, 1);
  assert.ok(fs.existsSync(reclassification.backup));
  await window.waitForFunction(() => !document.querySelector('.scan-progress'));
  assert.deepEqual(errors, []);
  await window.screenshot({ path: path.join(root, 'build/desktop-test-preview.png') });
  console.log('Desktop validado: catálogo, capas, busca, favoritos persistentes, detalhes, evidências, MSC, alas por família, classificação, recentes, lista, temas, 850×640, exportação, logs, cancelamento, reclassificação com backup, escolhas manuais e isolamento.');
}

main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (application) await application.close();
  fs.rmSync(fixture, { recursive: true, force: true });
});
