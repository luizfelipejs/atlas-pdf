import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AnimatePresence, MotionConfig, motion } from 'motion/react';
import { ArrowDownToLine, ArrowRight, ArrowUpRight, BookOpen, Check, CheckCheck, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, Command, Copy, FileText, FolderOpen, Grid2X2, Heart, History, Layers3, Library, List, LoaderCircle, Maximize2, Minus, Moon, MoreHorizontal, Plus, RefreshCw, Search, Sparkles, Sun, X } from 'lucide-react';
import './style.css';
import { SubjectAreas, SubjectEvidence } from './subjects.jsx';

const api = window.atlas;
const normalize = (text) => String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const format = (number) => Number(number || 0).toLocaleString('pt-BR');
const collator = new Intl.Collator('pt-BR', { sensitivity: 'base', numeric: true });
const colors = ['sage', 'blue', 'clay', 'lilac', 'amber'];
const tone = (text = '') => colors[[...text].reduce((sum, char) => sum + char.charCodeAt(0), 0) % colors.length];
function readSetting(key, fallback) {
  try { return JSON.parse(localStorage.getItem(`atlas.${key}`)) ?? fallback; } catch { return fallback; }
}
function useSetting(key, initial) {
  const [value, setValue] = useState(() => readSetting(key, initial));
  useEffect(() => { localStorage.setItem(`atlas.${key}`, JSON.stringify(value)); }, [key, value]);
  return [value, setValue];
}

function Orbit({ small = false }) {
  return <div className={`orbit-art ${small ? 'small' : ''}`} aria-hidden="true">
    <div className="orbit-grid" />
    <div className="orbit-ring ring-one"><i /></div><div className="orbit-ring ring-two"><i /></div>
    <div className="orbit-ring ring-three" /><span className="orbit-star star-one">✦</span><span className="orbit-star star-two">✧</span>
    <div className="orbit-book book-back"><span>∑</span></div><div className="orbit-book book-front"><span className="book-rule" /><span>ATLAS</span><b>∞</b><small>UM MUNDO DE IDEIAS</small></div>
    <span className="orbit-note">A curiosidade não tem limites.</span>
  </div>;
}

function Cover({ book, large = false }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [book.id, book.thumbnail]);
  return <div className={`cover-stage ${tone(book.category)} ${large ? 'large' : ''}`}>
    <div className={`cover-book ${book.thumbnail && !failed ? 'has-image' : 'generated'}`}>
      {book.thumbnail && !failed ? <img src={book.thumbnail} alt={`Capa de ${book.title}`} loading={large ? 'eager' : 'lazy'} onError={() => setFailed(true)} /> : <><small>{book.category_label || book.category}</small><strong>{book.title}</strong><span className="cover-symbol">{['∑', '∫', 'π', '∞', '∂'][colors.indexOf(tone(book.category))]}</span><span className="cover-author">{book.author || 'Biblioteca Atlas'}</span></>}
      <div className="book-spine" />
    </div>
    {book.missing && <span className="missing-badge">Arquivo ausente</span>}
  </div>;
}

function BookCard({ book, index, favorite, onFavorite, onSelect, view }) {
  return <motion.article data-book-card className={`book-card ${view === 'list' ? 'list-card' : ''}`} initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay: Math.min(index, 10) * 0.035 }}>
    <button className="cover-button" onClick={() => onSelect(book.id)} aria-label={`Ver detalhes de ${book.title}`}><Cover book={book} /><span className="cover-open"><ArrowUpRight size={18} /></span></button>
    <button className={`favorite-button ${favorite ? 'is-favorite' : ''}`} onClick={() => onFavorite(book.id)} aria-label={`${favorite ? 'Remover dos' : 'Adicionar aos'} favoritos: ${book.title}`} aria-pressed={favorite}><Heart size={16} fill={favorite ? 'currentColor' : 'none'} /></button>
    <div className="book-info"><span className={`category-label ${tone(book.category)}`}><i />{book.category_label || book.category}</span><button className="book-title" onClick={() => onSelect(book.id)} title={book.title}>{book.title}</button><span className="book-author" title={book.author}>{book.author || 'Autor não informado'}</span><div className="book-meta"><span>{book.pages ? `${format(book.pages)} páginas` : 'PDF'}<span className="meta-dot">·</span>{(book.size / 1048576).toFixed(1).replace('.', ',')} MB</span>{book.manual ? <span title="Categoria escolhida manualmente"><CheckCheck size={13} /></span> : book.category === 'Revisar' ? <span className="review-dot" title="Classificação a revisar" /> : <Check size={13} />}</div></div>
  </motion.article>;
}

function Detail({ book, categories, subjects, favorite, onFavorite, onClose, onAction, onCategory }) {
  const dialog = useRef(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const keywords = (book.keywords || []).slice(0, 8);
  const highest = Math.max(1, ...keywords.map((item) => item.count));
  const structure = book.analysis?.structure;
  return <motion.dialog ref={dialog} className="detail-dialog" aria-labelledby="detail-title" onCancel={(event) => { event.preventDefault(); onClose(); }} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <motion.aside className="detail-panel" initial={{ x: 70 }} animate={{ x: 0 }} exit={{ x: 70 }} transition={{ type: 'spring', stiffness: 340, damping: 35 }}>
      <div className="detail-top"><span>POR DENTRO DO LIVRO</span><button className="icon-button" onClick={onClose} aria-label="Fechar detalhes"><X size={20} /></button></div>
      <Cover book={book} large />
      <div className="detail-content"><div className="detail-category"><span className={`category-label ${tone(book.category)}`}><i />{book.category_label || book.category}</span><button className={`icon-button ${favorite ? 'is-favorite' : ''}`} onClick={() => onFavorite(book.id)} aria-label="Alternar favorito" aria-pressed={favorite}><Heart size={19} fill={favorite ? 'currentColor' : 'none'} /></button></div>
        <h2 id="detail-title">{book.title}</h2><p className="detail-author">{book.author || 'Autor não informado'}</p>
        <div className="detail-numbers"><div><strong>{format(book.pages)}</strong><span>páginas</span></div><div><strong>{(book.size / 1048576).toFixed(1).replace('.', ',')}</strong><span>MB</span></div><div><strong>{book.manual ? 'Manual' : ['new_theme', 'retained_theme', 'identical_content'].includes(book.analysis?.semantic?.source) ? 'Tema' : `${Math.round(book.confidence * 100)}%`}</strong><span>{book.manual ? 'classificação' : book.analysis?.semantic?.source === 'centroid' ? 'similaridade' : ['new_theme', 'retained_theme', 'identical_content'].includes(book.analysis?.semantic?.source) ? 'agrupamento automático' : 'estimativa de área'}</span></div></div>
        <button className="primary-button open-pdf" disabled={book.missing} onClick={() => onAction(() => api.openBook(book.id))}><BookOpen size={18} />{book.missing ? 'Arquivo não localizado' : 'Abrir PDF'}<ArrowUpRight size={18} /></button>
        <div className="detail-file-actions"><button className="secondary-button" onClick={() => onAction(() => api.revealBook(book.id))}><FolderOpen size={16} />Abrir pasta</button><button className="secondary-button" onClick={() => onAction(() => api.copyPath(book.id), 'Caminho copiado.')}><Copy size={15} />Copiar caminho</button></div>
        <section className="detail-section"><h3><FolderOpen size={15} />Localização</h3><p className="path-text">{book.path}</p></section>
        {structure && <section className="detail-section document-structure"><h3><FileText size={15} />Tipo de documento</h3>
          <p className="document-kind">{structure.status === 'unavailable' ? 'Não identificado' : structure.kind_label || 'Documento'}</p>
          {structure.title && <p><strong>Título lido:</strong> {structure.title}</p>}
          {structure.stats?.pages_read > 0 && <p>Estrutura analisada em {format(structure.stats.pages_read)} páginas.</p>}
          {structure.status === 'partial' && <p>Leitura parcial; o formato é uma sugestão automática.</p>}
          {(structure.headings?.length > 0 || structure.toc?.length > 0) && <details><summary>Ver assuntos na estrutura</summary><ul>
            {[...new Set([...(structure.headings || []).map((item) => item.text), ...(structure.toc || [])])].slice(0, 10).map((heading) => <li key={heading}>{heading}</li>)}
          </ul></details>}
        </section>}
        <section className="detail-section"><h3><Sparkles size={15} />Classificação local</h3><p>{book.reason || 'Este documento ainda não tem evidências suficientes para classificação.'}</p><label className="field-label" htmlFor="category-edit">Ajustar área da matemática</label><select id="category-edit" value={book.category} disabled={saving} onChange={async (event) => { setSaving(true); await onCategory(book.id, event.target.value); setSaving(false); }}>{categories.map((category) => <option key={category} value={category}>{subjects.find((item) => item.name === category)?.label || category}</option>)}</select>{saving && <span className="saving-label">Salvando…</span>}</section>
        <SubjectEvidence book={book} subjects={subjects} />
        {keywords.length > 0 && <section className="detail-section"><h3>Palavras mais frequentes</h3><div className="keyword-chart">{keywords.map((item) => <div className="keyword-row" key={item.word}><span>{item.word}</span><div><motion.i initial={{ scaleX: 0 }} animate={{ scaleX: item.count / highest }} transition={{ duration: 0.6 }} /></div><b>{format(item.count)}</b></div>)}</div></section>}
      </div>
    </motion.aside>
  </motion.dialog>;
}

function App() {
  const [catalog, setCatalog] = useState({ books: [], categories: [], stats: {} });
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [route, setRoute] = useState('all');
  const [category, setCategory] = useState('all');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useSetting('sort', 'title');
  const [view, setView] = useSetting('view', 'grid');
  const [theme, setTheme] = useSetting('theme', 'light');
  const [favorites, setFavorites] = useSetting('favorites', []);
  const [recent, setRecent] = useSetting('recent', []);
  const [selected, setSelected] = useState(null);
  const [page, setPage] = useState(0);
  const [menu, setMenu] = useState(false);
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [exporting, setExporting] = useState(false);
  const search = useRef(null);
  const scroll = useRef(null);
  const menuRef = useRef(null);
  const { books, categories } = catalog;
  const favoriteSet = useMemo(() => new Set(favorites), [favorites]);
  const selectedBook = books.find((book) => book.id === selected);
  const areaCounts = useMemo(() => categories.map((name) => ({ name, label: catalog.subjects?.find((item) => item.name === name)?.label || name, count: books.filter((book) => book.category === name).length })).sort((a, b) => b.count - a.count), [books, categories, catalog.subjects]);
  const reviewCount = books.filter((book) => book.category === 'Revisar').length;
  const activeAreas = areaCounts.filter((area) => area.count > 0 && !['Revisar', 'Outros assuntos'].includes(area.name));
  const notify = (message, error = false) => setToast({ message, error, key: Date.now() });

  async function load() {
    try {
      if (!api) throw new Error('Abra o Atlas pelo aplicativo desktop para acessar seus PDFs.');
      const data = await api.catalog();
      setCatalog(data); setBusy(data.scanning); setLoadError(''); setLoaded(true);
      window.__ATLAS_READY__ = true;
    } catch (error) { setLoadError(error.message); setLoaded(true); }
  }
  useEffect(() => { load(); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => api?.onScan((event) => {
    if (event.type === 'progress') { setBusy(true); setProgress(event); }
    if (event.type === 'complete') {
      setBusy(false); setProgress(null); load();
      notify(event.cancelled ? 'Busca cancelada. Resultados concluídos foram salvos.' : event.changed !== undefined ? `Reclassificação concluída: ${format(event.changed)} livros mudaram de ala${event.warnings ? ` · ${format(event.warnings)} avisos nos logs` : ''}.` : `Busca concluída: ${format(event.total)} PDFs encontrados${event.warnings ? ` · ${format(event.warnings)} avisos nos logs` : ''}.`);
    }
    if (event.type === 'error') { setBusy(false); setProgress(null); notify(event.message, true); }
  }), []);
  useEffect(() => {
    const keyboard = (event) => {
      if ((event.ctrlKey || event.metaKey) && ['k', 'f'].includes(event.key.toLowerCase())) { event.preventDefault(); search.current?.focus(); search.current?.select(); }
      if (event.key === 'Escape') setMenu(false);
    };
    const outside = (event) => { if (!menuRef.current?.contains(event.target)) setMenu(false); };
    window.addEventListener('keydown', keyboard); window.addEventListener('pointerdown', outside);
    return () => { window.removeEventListener('keydown', keyboard); window.removeEventListener('pointerdown', outside); };
  }, []);
  useEffect(() => { setPage(0); }, [route, query, category, sort, favorites]);

  const filtered = useMemo(() => {
    const needle = normalize(query);
    return books.filter((book) =>
      (route !== 'review' || book.category === 'Revisar') &&
      (route !== 'favorites' || favoriteSet.has(book.id)) &&
      (route !== 'recent' || recent.includes(book.id)) &&
      (category === 'all' || book.category === category) &&
      (!needle || normalize([book.title, book.author, book.path, book.category_label, book.analysis?.topic?.label, ...(book.analysis?.topic?.keyphrases || []).map((item) => item.phrase), ...book.keywords.map((item) => item.word)].join(' ')).includes(needle))
    ).sort((a, b) => route === 'recent' ? recent.indexOf(a.id) - recent.indexOf(b.id) : sort === 'recent' ? collator.compare(b.scanned_at, a.scanned_at) || collator.compare(a.title, b.title) : sort === 'pages' ? b.pages - a.pages : collator.compare(a.title, b.title));
  }, [books, route, category, query, sort, favoriteSet, recent]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / 24));
  const safePage = Math.min(page, pageCount - 1);
  const visible = filtered.slice(safePage * 24, (safePage + 1) * 24);
  const titles = { all: 'Sua biblioteca', areas: 'Áreas da matemática', review: 'Um olhar mais atento', favorites: 'Perto de você', recent: 'Suas últimas descobertas' };
  const subtitles = { all: 'Cada livro, uma nova possibilidade.', areas: 'Encontre conexões. Explore uma nova área.', review: 'Documentos que precisam da sua ajuda para encontrar uma área.', favorites: 'Os livros que você quer ter sempre à mão.', recent: 'Os últimos livros que você explorou no Atlas.' };

  function navigate(next) { setRoute(next); setCategory('all'); setQuery(''); setPage(0); scroll.current?.scrollTo({ top: 0, behavior: 'smooth' }); }
  function selectBook(id) { setSelected(id); setRecent((previous) => [id, ...previous.filter((item) => item !== id)].slice(0, 80)); }
  function toggleFavorite(id) { setFavorites((previous) => previous.includes(id) ? previous.filter((item) => item !== id) : [...previous, id]); }
  async function action(callback, success) { try { const result = await callback(); if (success) notify(success); return result; } catch (error) { notify(error.message, true); } }
  async function scan(mode = 'defaults') {
    setMenu(false); setBusy(true); setProgress({ phase: mode === 'force' ? 'classify' : 'discover', message: mode === 'folder' ? 'Escolha uma pasta para explorar…' : mode === 'force' ? 'Preparando reclassificação do acervo…' : 'Preparando a busca…', current: 0, total: 0 });
    try { const result = await api.scan(mode); if (result.cancelled) { setBusy(false); setProgress(null); } }
    catch (error) { setBusy(false); setProgress(null); notify(error.message, true); }
  }
  async function exportCatalog() {
    setExporting(true);
    await action(async () => { const file = await api.exportCatalog(); if (file) notify('Catálogo exportado com sucesso.'); });
    setExporting(false);
  }
  const navItems = [
    { id: 'all', icon: Library, title: 'Todos os livros', count: books.length },
    { id: 'areas', icon: Layers3, title: 'Áreas da matemática' },
    { id: 'favorites', icon: Heart, title: 'Favoritos', count: books.filter((book) => favoriteSet.has(book.id)).length },
    { id: 'recent', icon: History, title: 'Recentes' },
    { id: 'review', icon: CircleHelp, title: 'Para revisar', count: reviewCount },
  ];

  return <MotionConfig reducedMotion="user"><div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-mark">a<span>✦</span></div><div><strong>atlas<span>pdf</span></strong><small>IDEIAS ENCONTRAM SEU LUGAR.</small></div></div>
      <div className="workspace-label"><span className="workspace-icon"><BookOpen size={17} /></span><div><strong>Biblioteca pessoal</strong><span>Seu espaço de conhecimento</span></div><span className="local-dot" title="Armazenamento local" /></div>
      <div className="nav-label">EXPLORAR</div><nav aria-label="Navegação principal">{navItems.map(({ id, icon: Icon, title, count }) => <button key={id} className={`nav-item ${route === id ? 'active' : ''}`} onClick={() => navigate(id)}>{route === id && <motion.span className="nav-indicator" layoutId="navigation" transition={{ type: 'spring', stiffness: 450, damping: 38 }} />}<Icon size={18} /><span>{title}</span>{count !== undefined && <b>{format(count)}</b>}</button>)}</nav>
      <div className="nav-label areas-label">NO SEU ACERVO<span>{activeAreas.length}</span></div><div className="sidebar-areas">{activeAreas.slice(0, 5).map((area) => <button key={area.name} className={category === area.name ? 'selected-area' : ''} onClick={() => { setRoute('all'); setCategory(area.name); setQuery(''); }}><i className={tone(area.name)} /><span>{area.label}</span><small>{format(area.count)}</small></button>)}{activeAreas.length > 5 && <button className="see-areas" onClick={() => navigate('areas')}>Explorar todas as áreas<ArrowRight size={14} /></button>}</div>
      <div className="sidebar-bottom"><div className="local-card"><span className="local-card-icon"><CheckCheck size={20} /></span><strong>Seu acervo é seu.</strong><p>PDFs e descobertas ficam<br />neste computador.</p><span className="local-chip"><i />100% local</span></div><div className="sidebar-tools"><button onClick={() => action(() => api.openLogs())}><FileText size={16} />Logs de busca</button><button className="theme-button" aria-label={theme === 'light' ? 'Ativar tema escuro' : 'Ativar tema claro'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}</button></div><div className="sidebar-version">ATLAS PDF<span>3.0</span></div></div>
    </aside>
    <div className="main-shell"><header className="titlebar"><div className="breadcrumb"><span>Meu espaço</span><ChevronRight size={13} /><strong>{route === 'areas' ? 'Áreas da matemática' : 'Biblioteca'}</strong></div><div className="window-controls"><button aria-label="Minimizar" onClick={() => api?.windowAction('minimize')}><Minus size={15} /></button><button aria-label="Maximizar ou restaurar" onClick={() => api?.windowAction('maximize')}><Maximize2 size={13} /></button><button aria-label="Fechar Atlas" onClick={() => api?.windowAction('close')}><X size={16} /></button></div></header>
      <main className="main-scroll" ref={scroll}><div className="page-content">
        <div className="page-heading"><div><div className="eyebrow"><span />SEU CONHECIMENTO, CONECTADO</div><h1>{titles[route]}</h1><p>{subtitles[route]}</p></div><div className="heading-actions"><button className="secondary-button export-button" disabled={exporting || !loaded || Boolean(loadError)} onClick={exportCatalog}>{exporting ? <LoaderCircle size={16} className="spinning" /> : <ArrowDownToLine size={16} />}<span>Exportar catálogo</span></button><div className="scan-actions" ref={menuRef}><button className="primary-button" disabled={busy || Boolean(loadError)} onClick={() => scan()}>{busy ? <LoaderCircle className="spinning" size={17} /> : <Plus size={18} />}<span>{busy ? 'Buscando…' : 'Buscar PDFs'}</span></button><button className="primary-button scan-dropdown" disabled={busy || Boolean(loadError)} aria-label="Mais opções de busca" aria-expanded={menu} onClick={() => setMenu(!menu)}><ChevronDown size={15} /></button><AnimatePresence>{menu && <motion.div className="dropdown-menu" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}><span>ONDE EXPLORAR?</span><button onClick={() => scan('folder')}><FolderOpen size={16} />Adicionar uma pasta</button><button onClick={() => scan('home')}><Search size={16} />Toda a pasta pessoal</button><button onClick={() => scan('computer')}><Layers3 size={16} />Buscar no computador</button><hr /><button onClick={() => scan('force')}><RefreshCw size={16} />Reclassificar PDFs</button></motion.div>}</AnimatePresence></div></div></div>
        <AnimatePresence>{busy && <motion.section className="scan-progress" role="status" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}><div className="scan-progress-icon"><Search size={19} /></div><div className="scan-progress-text"><strong>{progress?.message || 'Buscando PDFs…'}</strong><span title={progress?.path}>{progress?.path || 'Seus resultados serão salvos automaticamente.'}</span><div className={`progress-track ${!progress?.total ? 'indeterminate' : ''}`}><motion.i animate={{ width: progress?.total ? `${Math.min(100, progress.current / progress.total * 100)}%` : '35%' }} /></div></div><small>{progress?.total ? `${format(progress.current)} / ${format(progress.total)}` : 'Em andamento'}</small><button className="secondary-button" onClick={() => action(() => api.cancelScan())}>Cancelar</button></motion.section>}</AnimatePresence>
        {loadError && <div className="error-box" role="alert"><CircleHelp size={21} /><div><strong>Não foi possível abrir a biblioteca</strong><p>{loadError}</p></div><button className="secondary-button" onClick={load}>Tentar novamente</button></div>}
        {route === 'all' && category === 'all' && !query && <motion.section className="hero" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}><div className="hero-content"><span className="hero-kicker"><Sparkles size={13} />MAIS ESPAÇO PARA DESCOBRIR</span><h2>Um universo de<br /><em>conhecimento.</em></h2><p>Seus livros organizados. Suas ideias em movimento.<br />Explore a matemática, uma descoberta de cada vez.</p><button onClick={() => navigate('areas')}>Explore suas áreas<ArrowUpRight size={17} /></button></div><Orbit /></motion.section>}
        <div className="stats-row"><div className="stat-item"><span className="stat-icon sage"><Library size={20} /></span><div><strong>{loaded ? format(books.length) : '—'}</strong><span>livros no seu universo</span></div><span className="stat-caption">PDFs indexados</span></div><div className="stat-item"><span className="stat-icon blue"><Layers3 size={20} /></span><div><strong>{loaded ? format(activeAreas.length) : '—'}</strong><span>áreas para explorar</span></div><span className="stat-caption">Conexões possíveis</span></div><button className="stat-item stat-review" onClick={() => navigate('review')}><span className="stat-icon amber"><CircleHelp size={20} /></span><div><strong>{loaded ? format(reviewCount) : '—'}</strong><span>livros para revisar</span></div><ArrowUpRight size={18} /></button></div>
        <section className="collection"><div className="collection-heading"><h2>{route === 'areas' ? 'Encontre seu próximo assunto' : category !== 'all' ? category : route === 'favorites' ? 'Seus favoritos' : route === 'review' ? 'Para revisar' : route === 'recent' ? 'Explorados recentemente' : 'Na sua estante'}<span>{route === 'areas' ? activeAreas.length : format(filtered.length)}</span></h2><span className="collection-hint">{route === 'areas' ? 'Organizado a partir do conteúdo dos PDFs' : 'Boas ideias começam com um livro.'}</span></div>
          {route !== 'areas' && <div className="collection-toolbar"><div className="search-box"><Search size={18} /><input ref={search} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Busque um título, autor ou ideia…" aria-label="Pesquisar livros" />{query ? <button className="icon-button" aria-label="Limpar busca" onClick={() => setQuery('')}><X size={15} /></button> : <kbd>Ctrl K</kbd>}</div><select aria-label="Filtrar por área" value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">Todas as áreas</option>{categories.map((name) => <option key={name} value={name}>{catalog.subjects?.find((item) => item.name === name)?.label || name}</option>)}</select><select className="sort-select" aria-label="Ordenar livros" value={sort} onChange={(event) => setSort(event.target.value)}><option value="title">Título A–Z</option><option value="recent">Mais recentes</option><option value="pages">Mais páginas</option></select><div className="view-switch"><button aria-label="Exibir em grade" aria-pressed={view === 'grid'} className={view === 'grid' ? 'active' : ''} onClick={() => setView('grid')}><Grid2X2 size={17} /></button><button aria-label="Exibir em lista" aria-pressed={view === 'list'} className={view === 'list' ? 'active' : ''} onClick={() => setView('list')}><List size={18} /></button></div></div>}
          {!loaded ? <div className="books-grid skeletons">{Array.from({ length: 8 }, (_, index) => <div className="skeleton-card" key={index}><div /><span /><span /></div>)}</div> : route === 'areas' ? <SubjectAreas areas={areaCounts} subjects={catalog.subjects || []} onSelect={(name) => { setRoute('all'); setCategory(name); }} /> : visible.length ? <div className={view === 'list' ? 'books-list' : 'books-grid'} key={`${route}:${category}:${query}:${sort}:${safePage}:${view}`}>{visible.map((book, index) => <BookCard book={book} index={index} key={book.id} favorite={favoriteSet.has(book.id)} onFavorite={toggleFavorite} onSelect={selectBook} view={view} />)}</div> : <motion.div className="empty-state" initial={{ opacity: 0 }} animate={{ opacity: 1 }}><span className="empty-icon">{route === 'favorites' ? <Heart size={30} /> : query ? <Search size={30} /> : <BookOpen size={30} />}</span><h3>{query ? 'Uma nova busca, uma nova possibilidade.' : route === 'favorites' ? 'Guarde suas próximas descobertas.' : route === 'recent' ? 'Sua jornada começa aqui.' : route === 'review' ? 'Tudo em seu lugar.' : 'Sua biblioteca está esperando por você.'}</h3><p>{query ? 'Tente outras palavras ou explore todas as áreas.' : route === 'favorites' ? 'Toque no coração de um livro para encontrá-lo aqui.' : route === 'recent' ? 'Abra os detalhes de um livro e reencontre-o aqui depois.' : route === 'review' ? 'Nenhum livro precisa de revisão neste filtro.' : 'Busque PDFs no computador ou adicione uma pasta para começar.'}</p>{query || category !== 'all' ? <button className="secondary-button" onClick={() => { setQuery(''); setCategory('all'); }}>Limpar filtros</button> : <button className="secondary-button" disabled={busy} onClick={() => route === 'all' ? scan() : navigate('all')}>{route === 'all' ? 'Buscar meus PDFs' : 'Explorar a biblioteca'}<ArrowRight size={15} /></button>}</motion.div>}
          {route !== 'areas' && filtered.length > 0 && <div className="pagination"><span>Mostrando {format(safePage * 24 + 1)}–{format(Math.min((safePage + 1) * 24, filtered.length))} de {format(filtered.length)} livros</span><div><button className="icon-button" aria-label="Página anterior" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}><ChevronLeft size={17} /></button><span>{safePage + 1}<i>/</i>{pageCount}</span><button className="icon-button" aria-label="Próxima página" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}><ChevronRight size={17} /></button></div></div>}
        </section>
        <footer className="page-footer"><span><span className="local-dot" />{busy ? 'Explorando seu computador…' : 'Tudo salvo neste dispositivo'}</span><span>Feito para quem nunca para de aprender.<span className="footer-star">✦</span></span></footer>
      </div></main>
    </div>
    <AnimatePresence>{selectedBook && <Detail key={selectedBook.id} book={selectedBook} categories={categories} subjects={catalog.subjects || []} favorite={favoriteSet.has(selectedBook.id)} onFavorite={toggleFavorite} onClose={() => setSelected(null)} onAction={action} onCategory={async (id, next) => action(async () => { setCatalog(await api.setCategory(id, next)); notify('Classificação atualizada.'); })} />}</AnimatePresence>
    <AnimatePresence>{toast && <motion.div className={`toast ${toast.error ? 'error' : ''}`} key={toast.key} role={toast.error ? 'alert' : 'status'} initial={{ opacity: 0, y: 20, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 10 }}>{toast.error ? <CircleHelp size={18} /> : <Check size={18} />}<span>{toast.message}</span><button aria-label="Dispensar mensagem" onClick={() => setToast(null)}><X size={16} /></button></motion.div>}</AnimatePresence>
  </div></MotionConfig>;
}

createRoot(document.getElementById('root')).render(<App />);
