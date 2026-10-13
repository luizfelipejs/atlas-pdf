const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('atlas', Object.freeze({
  catalog: () => ipcRenderer.invoke('atlas:catalog'),
  scan: (mode) => ipcRenderer.invoke('atlas:scan', mode),
  cancelScan: () => ipcRenderer.invoke('atlas:cancel'),
  setCategory: (id, category) => ipcRenderer.invoke('atlas:category', id, category),
  openBook: (id) => ipcRenderer.invoke('atlas:open', id),
  revealBook: (id) => ipcRenderer.invoke('atlas:reveal', id),
  copyPath: (id) => ipcRenderer.invoke('atlas:copy', id),
  exportCatalog: () => ipcRenderer.invoke('atlas:export'),
  openLogs: () => ipcRenderer.invoke('atlas:logs'),
  windowAction: (action) => ipcRenderer.invoke('atlas:window', action),
  onScan: (callback) => {
    const listener = (_event, value) => callback(value);
    ipcRenderer.on('atlas:progress', listener);
    return () => ipcRenderer.removeListener('atlas:progress', listener);
  },
}));
