import React from 'react';
import { motion } from 'motion/react';
import { ArrowUpRight, CheckCheck, GitBranch, Layers3, Sparkles } from 'lucide-react';

const tones = ['sage', 'blue', 'clay', 'lilac', 'amber'];

export function SubjectAreas({ areas, subjects, onSelect }) {
  const families = new Map();
  for (const area of areas.filter((item) => item.count)) {
    const subject = subjects.find((item) => item.name === area.name);
    const family = subject?.family || 'Organização do acervo';
    if (!families.has(family)) families.set(family, []);
    families.get(family).push({ ...area, label: subject?.label || area.name, msc: subject?.msc });
  }
  return <div className="subject-families">{[...families].map(([family, children]) => <section className="subject-family" key={family}>
    <div className="family-heading"><h3><Layers3 size={16} />{family}</h3><span>{children.reduce((sum, child) => sum + child.count, 0).toLocaleString('pt-BR')} livros · {children.length} alas</span></div>
    <div className="areas-grid">{children.map((area, index) => <motion.button className={`area-card ${tones[index % tones.length]}`} key={area.name} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * .025 }} onClick={() => onSelect(area.name)}>
      <span className="area-symbol">{['∑', '∫', 'π', '∞', '∂'][index % 5]}</span><ArrowUpRight size={19} />
      {area.msc && <span className="msc-code">MSC {area.msc}</span>}<h3>{area.label}</h3><p>{area.count.toLocaleString('pt-BR')} livros para descobrir</p>
      <div className="area-bar"><i style={{ width: `${area.count / Math.max(...children.map((item) => item.count)) * 100}%` }} /></div>
    </motion.button>)}</div>
  </section>)}</div>;
}

export function SubjectEvidence({ book, subjects }) {
  const analysis = book.analysis || {};
  const candidates = analysis.candidates || [];
  const subject = subjects.find((item) => item.name === book.category);
  const highest = Math.max(1, ...candidates.map((item) => item.score));
  const semantic = analysis.semantic;
  const topic = analysis.topic;
  const categoryLabel = (name) => subjects.find((item) => item.name === name)?.label || name;
  return <section className="detail-section subject-evidence">
    {topic?.label && <div className="topic-evidence">
      <h3><Sparkles size={16} />Tema sugerido pela leitura</h3>
      <p className="topic-name"><strong>{topic.label}</strong></p>
      <p className="evidence-note">{topic.needs_review ? 'Sugestão com evidência limitada: confira os trechos antes de confirmar o assunto.' : 'Expressão extraída do conteúdo e comparada com trechos distribuídos do livro.'}</p>
      <details className="evidence-details"><summary>Ver expressões e trechos de apoio<Chevron /></summary><div className="evidence-candidates">{topic.keyphrases?.map((item) => <div className="evidence-candidate" key={item.phrase}>
        <div><strong>{item.phrase}</strong><span>{item.count} ocorrências nas amostras</span></div>
        {item.evidence?.map((text, index) => <p key={index}>{text}</p>)}
      </div>)}</div></details>
    </div>}
    <h3><GitBranch size={16} />{book.manual ? 'Área escolhida por você' : 'Por que esta ala?'}</h3>
    {subject && <div className="subject-breadcrumb"><span>{subject.family}</span><span>→</span><strong>{categoryLabel(book.category)}</strong>{subject.msc && <small>MSC {subject.msc}</small>}</div>}
    {semantic?.status === 'ready' && <div className="semantic-evidence">
      <div className="evidence-overview"><span><Sparkles size={14} />{['new_theme', 'retained_theme'].includes(semantic.source) ? 'Tema descoberto pelo conteúdo' : semantic.source === 'centroid' ? 'Agrupado com livros semelhantes' : semantic.source === 'identical_content' ? 'Mesmo conteúdo de um livro catalogado' : 'Conteúdo comparado por similaridade'}</span><small>{semantic.chunks} trechos analisados</small></div>
      {semantic.candidates?.length > 0 && <details className="evidence-details"><summary>{semantic.stage === 'consolidação' ? 'Comparações antes do reagrupamento' : 'Comparar livros por tema'}<Chevron /></summary><div className="evidence-candidates">{semantic.candidates.map((item) => <div className="evidence-candidate" key={item.category}><div><strong>{categoryLabel(item.category)}</strong><span>{Math.round(item.similarity * 100)}% de similaridade</span></div><small>{item.quantity} conteúdos de referência · limiar de {Math.round((item.threshold ?? semantic.threshold) * 100)}%</small></div>)}</div></details>}
      <p className="evidence-note">Novos temas são criados conforme o acervo cresce. Similaridade não representa probabilidade de acerto.</p>
    </div>}
    {semantic?.status === 'unavailable' && <p className="evidence-note">A comparação semântica está indisponível. A classificação usou conceitos do conteúdo.</p>}
    {!candidates.length ? <p>{analysis.version ? 'Não foram encontrados conceitos suficientes para comparar as áreas. Confira o motivo da classificação abaixo.' : 'As evidências detalhadas estarão disponíveis depois da reclassificação deste PDF.'}</p> : <>
      <div className="evidence-overview"><span><Sparkles size={14} />{analysis.decision === 'ambiguous' ? 'Temas próximos: revisão recomendada' : book.manual ? 'Análise automática preservada abaixo' : candidates[0].title_match ? 'Título corroborado pelo conteúdo' : 'Conceitos identificados no conteúdo'}</span>
        <small>{analysis.sample_warning ? 'Amostragem parcial' : `Até ${analysis.sample_pages || 0} de ${analysis.total_pages || book.pages} páginas · ${analysis.sample_ranges?.length || 1} intervalos`}</small>
      </div>
      {analysis.secondary?.length > 0 && <div className="secondary-subjects"><span>ASSUNTOS RELACIONADOS</span><div>{analysis.secondary.map((item) => <span key={item.category} title={item.role}><GitBranch size={11} />{item.category}</span>)}</div></div>}
      <details className="evidence-details" open={analysis.decision === 'ambiguous'}><summary>Comparar evidências entre áreas<Chevron /></summary><div className="evidence-candidates">{candidates.slice(0, 4).map((item, index) => <div className="evidence-candidate" key={item.category}>
        <div><strong>{item.category}</strong><span>{item.score.toFixed(1)} pts</span></div>
        <div className="evidence-score"><motion.i initial={{ width: 0 }} animate={{ width: `${item.score / highest * 100}%` }} /></div>
        <small>{item.signals} conceitos · {item.sections}/{analysis.sections} trechos{item.role === 'ferramenta de apoio' ? ' · assunto de apoio' : index === 0 && analysis.decision === 'classified' ? ' · tema principal sugerido' : ''}</small>
        <p>{item.terms?.join(' · ')}</p>
      </div>)}</div></details>
      <p className="evidence-note"><CheckCheck size={13} />Pontuação de evidência, sem probabilidade calibrada. Categorias manuais têm prioridade.</p>
    </>}
  </section>;
}

function Chevron() { return <span aria-hidden="true">⌄</span>; }
