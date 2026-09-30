import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowDownLeft, ArrowRight, ArrowUpRight, Check, CheckCircle2, ChevronRight, Clock3, CreditCard, Database, FlaskConical, Layers3, LoaderCircle, MapPin, Music2, Radio, RotateCcw, ShieldCheck, Ticket, X, XCircle, Zap } from 'lucide-react';
import './style.css';

const money = n => new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 }).format(n);
const labels = { running: 'En proceso', confirmed: 'Confirmada', compensated: 'Compensada', compensating: 'Compensando', compensation_pending: 'Requiere reintento', failed: 'Falló', success: 'Completado', pending: 'Pendiente', reserved: 'Reservada', cancelled: 'Cancelada', charged: 'Cobrado', refunded: 'Reembolsado', absent: 'Sin operación', unavailable: 'No disponible' };
const serviceNames = { coordinator: 'Coordinador', reservations: 'Reservas', payments: 'Pagos', confirmations: 'Confirmaciones' };
const stepNames = { reservation: 'Reservar entrada', payment: 'Registrar pago', confirmation: 'Confirmar compra', refund: 'Reembolsar pago', release: 'Liberar reserva', compensation: 'Verificar estado' };

async function api(path, options = {}) {
  const response = await fetch('/api' + path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers }, signal: options.signal ?? AbortSignal.timeout(65000) });
  const data = await response.json();
  if (!response.ok && !data.id) throw new Error(typeof data.detail === 'string' ? data.detail : `Error HTTP ${response.status}`);
  return data;
}

function Badge({ status, children }) {
  return <span className={`badge ${status || ''}`}><span className="dot" />{children || labels[status] || status}</span>;
}

function App() {
  const [tab, setTab] = useState('tickets');
  const [catalog, setCatalog] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [purchases, setPurchases] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [evidence, setEvidence] = useState(null);
  const [eventId, setEventId] = useState('neon');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [online, setOnline] = useState(false);
  const [notice, setNotice] = useState('');
  const [showReset, setShowReset] = useState(false);
  const [count, setCount] = useState(50);
  const [delay, setDelay] = useState(2000);
  const [advanced, setAdvanced] = useState(false);
  const [failRefund, setFailRefund] = useState(false);
  const selectedRef = useRef(null);
  const run = metrics?.run;
  const locked = busy || !!metrics?.active_purchases || run?.status === 'running';
  const selected = purchases.find(p => p.id === selectedId);
  const event = catalog.find(e => e.id === eventId);

  const refresh = useCallback(async () => {
    const [cat, met, orders] = await Promise.allSettled([api('/catalog'), api('/metrics'), api('/purchases')]);
    if (cat.status === 'fulfilled') setCatalog(cat.value);
    if (met.status === 'fulfilled') { setMetrics(met.value); setOnline(true); } else setOnline(false);
    if (orders.status === 'fulfilled') setPurchases(orders.value);
    if (selectedRef.current) {
      try { setEvidence(await api(`/purchases/${selectedRef.current}/evidence`)); } catch { setEvidence(null); }
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer;
    const poll = async () => { await refresh(); if (!stopped) timer = setTimeout(poll, 700); };
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [refresh]);

  function select(id) { selectedRef.current = id; setSelectedId(id); setEvidence(null); }

  async function buy(fail = false) {
    setBusy(true); setError(''); setNotice('');
    const id = crypto.randomUUID();
    select(id);
    try {
      const result = await api('/purchases', { method: 'POST', body: JSON.stringify({ purchase_id: id, event_id: eventId, fail_confirmation: fail, fail_refund: fail && failRefund }) });
      setNotice(result.status === 'confirmed' ? '¡Tu entrada está confirmada! El recorrido se completó.' : result.status === 'compensated' ? 'Falla compensada: revisá la reserva y el pago debajo.' : 'La compensación quedó pendiente. Podés reintentarla.');
      await refresh();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function startLoad(scenario) {
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await api('/load', { method: 'POST', body: JSON.stringify({ scenario, count, payment_delay_ms: delay }) });
      setMetrics(old => ({ ...old, run: result }));
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function reset() {
    setBusy(true); setError(''); setShowReset(false);
    try { await api('/reset', { method: 'POST' }); select(null); setNotice('Datos restablecidos. Todo listo para otra demostración.'); await refresh(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function retry() {
    setBusy(true); setError('');
    try { await api(`/purchases/${selectedId}/retry`, { method: 'POST' }); await refresh(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  return <>
    <header className="topbar"><a className="brand" href="#" onClick={e => { e.preventDefault(); setTab('tickets'); }}><span className="brand-mark"><Ticket size={23} /></span>devoss<span className="brand-light">tickets</span><span className="brand-period">.</span></a>
      <nav aria-label="Navegación principal"><button className={tab === 'tickets' ? 'nav-active' : ''} onClick={() => setTab('tickets')}><Ticket size={16} /> Boletería</button><button className={tab === 'lab' ? 'nav-active' : ''} onClick={() => setTab('lab')}><FlaskConical size={16} /> Laboratorio <span className="tiny">LIVE</span></button></nav>
      <div className="header-right"><span className="team">EQUIPO 07 <b>DevOss</b></span><span className={`connection ${online ? '' : 'offline'}`}><span className="dot" />{online ? 'Sistema conectado' : 'Sin conexión'}</span></div>
    </header>
    <main>
      <div className="breadcrumb">DEVOPS EN ACCIÓN <span>/</span> {tab === 'tickets' ? 'CONSISTENCIA DISTRIBUIDA' : 'RESILIENCIA BAJO CARGA'} <span className="edition">LAB SERIES — 012 / 014</span></div>
      <section className="hero"><div><div className="eyebrow"><span className="line" /> {tab === 'tickets' ? 'BUENA MÚSICA. SISTEMAS QUE RESPONDEN.' : 'MENOS SUPOSICIONES. MÁS EVIDENCIA.'}</div><h1>{tab === 'tickets' ? <>El show sigue.<br /><span>Incluso si algo falla.</span></> : <>Poné el sistema<br /><span>bajo presión.</span></>}</h1><p>{tab === 'tickets' ? 'Elegí un show y seguí tu compra por dentro. Reservas, pagos y confirmaciones, coordinados paso a paso.' : 'Enviá una ráfaga de solicitudes, observá los límites y comprobá que el catálogo sigue disponible.'}</p></div>
        <div className="hero-note"><span className="orbit-icon">{tab === 'tickets' ? <Layers3 size={30} /> : <ShieldCheck size={30} />}</span><div><small>UN EXPERIMENTO REAL</small><strong>{tab === 'tickets' ? '4 servicios. Una compra.' : 'Aislar. Limitar. Responder.'}</strong><p>{tab === 'tickets' ? 'Una falla parcial no tiene por qué dejar todo a medias.' : 'La sobrecarga de pagos se queda en pagos.'}</p></div><ArrowUpRight size={20} /></div>
      </section>

      {!online && <div className="alert error"><Radio size={18} /> No se pudo conectar con la API. Iniciá los servicios; la conexión se reintenta automáticamente.</div>}
      {error && <div role="alert" className="alert error"><XCircle size={18} />{error}<button aria-label="Cerrar error" onClick={() => setError('')}><X size={16} /></button></div>}
      {notice && <div role="status" className="alert info"><CheckCircle2 size={18} />{notice}<button aria-label="Cerrar aviso" onClick={() => setNotice('')}><X size={16} /></button></div>}

      <div className="workspace-bar"><div className="workspace-title"><span className="live-dot" />{tab === 'tickets' ? 'Tu próxima experiencia' : 'Centro de experimentos'}<span className="muted">/ {tab === 'tickets' ? 'Temporada 2026' : 'Métricas en vivo'}</span></div><button className="text-button" disabled={locked || !online} onClick={() => setShowReset(true)}><RotateCcw size={14} /> Restablecer demo</button></div>

      {tab === 'tickets' ? <>
        <div className="store-layout"><section className="event-grid" aria-label="Shows disponibles">{catalog.map((item, i) => <button key={item.id} className={`event-card ${eventId === item.id ? 'selected' : ''}`} onClick={() => setEventId(item.id)} aria-pressed={eventId === item.id}>
          <div className={`poster poster-${item.id}`}><span className="poster-top">DEVOSS PRESENTA <ArrowUpRight size={15} /></span><span className="poster-number">0{i + 1}</span><div className="poster-art" /><div className="poster-name">{item.name.split(' ').map((word, j) => <React.Fragment key={j}>{word}<br /></React.Fragment>)}</div><span className="poster-bottom">BUENOS AIRES <span>2026</span></span></div>
          <div className="event-info"><div className="event-meta"><span>{item.genre}</span><span>{item.date}</span></div><h2>{item.name}</h2><p><MapPin size={13} />{item.venue}</p><div className="event-price"><strong>{money(item.price)} <small>/ entrada</small></strong><span className="select-check">{eventId === item.id ? <Check size={15} /> : <ArrowUpRight size={15} />}</span></div><div className="stock"><span className="dot" /><span data-testid={`stock-${item.id}`}>{item.stock}</span> disponibles</div></div>
        </button>)}</section>
        <aside className="checkout panel"><div className="panel-kicker"><Ticket size={16} /> TU ENTRADA <span>01</span></div><h2>Un lugar en el show.</h2><p className="muted">Elegí cómo querés probar la compra.</p><div className="receipt"><div><span>Evento</span><b>{event?.name || 'Cargando…'}</b></div><div><span>Cantidad</span><b>1 entrada general</b></div><div className="receipt-total"><span>Total simulado</span><strong>{money(event?.price || 0)}</strong></div></div><button className="button primary" disabled={locked || !online || !event?.stock} onClick={() => buy(false)}>{busy ? <LoaderCircle className="spin" size={17} /> : <Ticket size={17} />}Compra exitosa<ArrowRight size={17} /></button><button className="button secondary" disabled={locked || !online || !event?.stock} onClick={() => buy(true)}><Zap size={16} />Fallar confirmación<ArrowDownLeft size={16} /></button><p className="checkout-note"><ShieldCheck size={15} />Sin cargos reales. La segunda opción activa el recorrido de compensación.</p><button className="advanced-link" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}>Opciones del experimento <ChevronRight size={13} /></button>{advanced && <label className="checkbox"><input type="checkbox" checked={failRefund} disabled={locked} onChange={e => setFailRefund(e.target.checked)} />Hacer fallar también el reembolso para probar el reintento.</label>}</aside></div>
        <SagaPanel purchase={selected} evidence={evidence} busy={busy} retry={retry} />
        <History purchases={purchases} selectedId={selectedId} select={select} />
      </> : <>
        <div className="lab-layout"><aside className="panel lab-controls"><div className="panel-kicker"><FlaskConical size={16} /> MESA DE CONTROL</div><h2>Elegí la presión.</h2><p className="muted">Cada escenario aísla la protección que querés observar.</p><label className="range-label" htmlFor="count">Solicitudes concurrentes <b>{count}</b></label><input id="count" type="range" min="10" max="60" step="10" value={count} disabled={locked} onChange={e => setCount(+e.target.value)} /><div className="range-ends"><span>10 solicitudes</span><span>60 solicitudes</span></div><label className="range-label" htmlFor="delay">Demora de cada pago <b>{(delay / 1000).toFixed(1)} s</b></label><input id="delay" type="range" min="500" max="4000" step="500" value={delay} disabled={locked} onChange={e => setDelay(+e.target.value)} /><div className="range-ends"><span>Rápido</span><span>Consumidor lento</span></div>
          <button className="button primary" disabled={locked || !online} onClick={() => startLoad('rate')}><Zap size={17} />Probar rate limiting<ArrowRight size={16} /></button><button className="button secondary" disabled={locked || !online} onClick={() => startLoad('bulkhead')}><Layers3 size={17} />Saturar pagos<ArrowRight size={16} /></button><button className="button secondary" disabled={locked || !online} onClick={() => startLoad('backpressure')}><RotateCcw size={17} />Aplicar backpressure</button><button className="text-button unprotected" disabled={locked || !online} onClick={() => startLoad('unprotected')}>Comparar sin protecciones <ArrowUpRight size={14} /></button><div className="tip"><ShieldCheck size={19} /><p><b>El catálogo tiene su propio camino.</b> Durante la prueba lo consultamos cada ~200 ms para verificar que sigue respondiendo.</p></div>
        </aside><section className="lab-results"><div className="metric-grid"><Metric title="Pagos activos" value={metrics?.payments?.active ?? '—'} suffix="/ 3" icon={CreditCard} note={run?.scenario === 'unprotected' ? 'Límite desactivado en esta prueba' : 'Capacidad aislada para cobros'} /><Metric title="En espera" value={metrics?.payments?.waiting ?? '—'} suffix="/ 5" icon={Clock3} note="Cola acotada de pagos" /><Metric title="HTTP 429" value={run?.http_statuses?.['429'] || 0} icon={ShieldCheck} note="Exceso de frecuencia" /><Metric title="HTTP 503" value={run?.http_statuses?.['503'] || 0} icon={Layers3} note="Rechazos / fallas del servicio" /></div>
          <div className="panel chart-panel"><div className="section-heading"><div><div className="panel-kicker">AISLAMIENTO EN TIEMPO REAL</div><h2>El catálogo sigue en vivo.</h2></div><Badge status={run?.status === 'running' ? 'running' : 'success'}>{run?.status === 'running' ? 'Prueba en curso' : run ? 'Última ejecución' : 'Listo para probar'}</Badge></div><LoadChart samples={run?.samples || []} /><div className="chart-legend"><span><i className="legend-green" />Pagos activos</span><span><i className="legend-orange" />En cola</span><span><i className="legend-blue" />Latencia del catálogo (ms, eje derecho)</span></div></div>
          <RunResult run={run} />
        </section></div>
        <div className="concept-grid"><Concept number="01" title="Rate limiting" subtitle="¿Cuántas solicitudes por segundo?" text="Un bucket de 10 tokens por cliente. Cada segundo repone 10; cuando se agota, responde 429 con Retry-After." /><Concept number="02" title="Bulkhead" subtitle="¿Cuánto trabajo al mismo tiempo?" text="Pagos tiene 3 cupos propios y 5 lugares en espera. Si se llenan, rechaza con 503. El catálogo no comparte esos cupos." /><Concept number="03" title="Backpressure" subtitle="¿Qué hace el productor si no hay lugar?" text="La cola acotada devuelve presión al cliente: espera y reintenta hasta 3 veces. Los rechazos finales siguen visibles." /></div>
      </>}
      <section className="services-strip"><div><Database size={17} /><span>ESTADO DE LOS SERVICIOS</span></div>{Object.entries(serviceNames).map(([key, name]) => <span key={key}><span className={`dot ${online && metrics?.services?.[key] === 'ok' ? 'green' : 'red'}`} />{name}<small>{online && metrics?.services?.[key] === 'ok' ? 'online' : 'sin conexión'}</small></span>)}</section>
      <footer><span><b>devoss</b> / Ingeniería detrás de la experiencia.</span><span>TP DEVOPS · EQUIPO 7 <span className="footer-dot">✳</span> HECHO PARA EXPERIMENTAR</span></footer>
    </main>
    {showReset && <div className="modal-backdrop" onClick={() => setShowReset(false)}><section role="dialog" aria-modal="true" aria-labelledby="reset-title" className="modal panel" onClick={e => e.stopPropagation()}><RotateCcw size={25} /><h2 id="reset-title">¿Empezamos de nuevo?</h2><p>Se borrarán las compras y las métricas de la demo. Los tres shows volverán a tener 200 entradas.</p><div><button className="button secondary" onClick={() => setShowReset(false)}>Cancelar</button><button className="button primary" autoFocus onClick={reset}>Restablecer datos</button></div></section></div>}
  </>;
}

function SagaPanel({ purchase, evidence, busy, retry }) {
  const steps = [{ key: 'reservation', icon: Ticket, title: 'Reserva', service: 'reservations', compensator: 'release' }, { key: 'payment', icon: CreditCard, title: 'Pago', service: 'payments', compensator: 'refund' }, { key: 'confirmation', icon: CheckCircle2, title: 'Confirmación', service: 'confirmations' }];
  function stepState(step) {
    const events = purchase?.events || [];
    const compensated = events.filter(e => e.step === step.compensator).at(-1);
    if (compensated) return compensated.status === 'running' ? 'compensating' : compensated.status;
    return events.filter(e => e.step === step.key).at(-1)?.status || 'pending';
  }
  return <section className="panel saga-panel"><div className="section-heading"><div><div className="panel-kicker"><Activity size={15} /> SAGA EN VIVO <span className="outline-tag">TEMA 12</span></div><h2>Cada paso cuenta. Cada cambio también.</h2></div>{purchase ? <Badge status={purchase.status} /> : <Badge status="pending">Esperando una compra</Badge>}</div><div className="saga-flow"><div className="orchestrator"><div className="node-icon"><Layers3 size={23} /></div><b>Coordinador</b><small>Decide el siguiente paso</small></div><span className="flow-arrow"><ArrowRight size={23} /></span>{steps.map((step, i) => <React.Fragment key={step.key}><div className={`saga-node ${stepState(step)}`}><div className="node-top"><step.icon size={20} /><span>0{i + 1}</span></div><h3>{step.title}</h3><Badge status={stepState(step)} /><div className="db-caption"><Database size={12} />Base de datos propia</div></div>{i < 2 && <span className="flow-arrow"><ArrowRight size={23} /></span>}</React.Fragment>)}</div>
    {purchase?.events.some(e => e.step === 'refund' || e.step === 'release') && <div className="compensation-path"><ArrowDownLeft size={18} /><b>Vuelta compensatoria</b><span>Reembolsar pago</span><span>→</span><span>Liberar reserva</span><small>Operaciones nuevas que revierten los efectos anteriores</small></div>}
    {purchase ? <div className="saga-detail"><div className="timeline"><div className="detail-heading">BITÁCORA DE LA COMPRA <code>#{purchase.id.slice(0, 8)}</code></div>{purchase.events.map(e => <div className={`timeline-row ${e.status}`} key={e.seq}><span className="timeline-icon">{e.status === 'running' ? <Clock3 size={13} /> : e.status === 'failed' ? <X size={13} /> : <Check size={13} />}</span><time>{new Date(e.at).toLocaleTimeString('es-AR', { hour12: false })}</time><div><b>{stepNames[e.step]}</b><p>{e.message}</p></div></div>)}</div><div className="evidence"><div className="detail-heading">EVIDENCIA EN CADA SERVICIO</div>{steps.map(step => <div className="evidence-row" key={step.key}><span><step.icon size={15} />{step.title}</span><strong>{labels[evidence?.[step.service]?.status] || 'Consultando…'}</strong></div>)}<p><Database size={14} />Estos estados se consultan en las bases de cada servicio.</p>{purchase.status === 'compensation_pending' && <button className="button secondary" disabled={busy} onClick={retry}><RotateCcw size={15} />Reintentar compensación</button>}</div></div> : <div className="saga-empty"><span><Radio size={18} /></span><p><b>El recorrido de tu compra aparece acá.</b> Probá una compra exitosa o provocá una falla para ver cómo se deshacen los cambios.</p><span className="empty-code">RESERVAR → PAGAR → CONFIRMAR</span></div>}
  </section>;
}

function History({ purchases, selectedId, select }) {
  const [expanded, setExpanded] = useState(false);
  if (!purchases.length) return null;
  return <section className="history panel"><div className="section-heading"><h2>Últimas compras <span className="outline-tag">{purchases.length}</span></h2><button className="text-button" onClick={() => setExpanded(!expanded)}>{expanded ? 'Mostrar menos' : 'Ver historial'}<ChevronRight size={14} /></button></div><div className="table-scroll"><table><thead><tr><th>COMPRA</th><th>EVENTO</th><th>HORA</th><th>ESTADO</th><th>RECORRIDO</th></tr></thead><tbody>{purchases.slice(0, expanded ? 100 : 4).map(p => <tr key={p.id} className={selectedId === p.id ? 'selected-row' : ''}><td><code>#{p.id.slice(0, 8)}</code></td><td>{{ neon: 'Neon Nights', indie: 'Indie al Parque', jazz: 'Blue Sessions' }[p.body.event_id]}</td><td>{new Date(p.created_at).toLocaleTimeString('es-AR')}</td><td><Badge status={p.status} /></td><td><button className="text-button" onClick={() => select(p.id)}>Inspeccionar <ArrowUpRight size={14} /></button></td></tr>)}</tbody></table></div></section>;
}

function Metric({ title, value, suffix, icon: Icon, note }) {
  return <div className="panel metric"><div>{title}<Icon size={17} /></div><strong>{value}<span>{suffix}</span></strong><small>{note}</small></div>;
}

function LoadChart({ samples }) {
  const width = 720, height = 210, left = 32, right = 670, top = 16, bottom = 180;
  const maxWork = Math.max(6, ...samples.map(s => s.active + 1));
  const maxLatency = Math.max(50, ...samples.map(s => s.latency_ms));
  const points = (key, max) => samples.map((s, i) => `${left + (i / Math.max(1, samples.length - 1)) * (right - left)},${bottom - s[key] / max * (bottom - top)}`).join(' ');
  return <div className="chart"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Gráfico de pagos activos, cola y latencia del catálogo durante la ejecución">{[0, .25, .5, .75, 1].map(f => <g key={f}><line x1={left} x2={right} y1={bottom - f * (bottom - top)} y2={bottom - f * (bottom - top)} stroke="#e7eae2" strokeDasharray="3 4" /><text x={left - 10} y={bottom - f * (bottom - top) + 4} textAnchor="end">{Math.round(f * maxWork)}</text><text x={right + 8} y={bottom - f * (bottom - top) + 4}>{Math.round(f * maxLatency)}</text></g>)}{samples.length > 0 && <><polyline points={points('active', maxWork)} className="line-active" /><polyline points={points('waiting', maxWork)} className="line-waiting" /><polyline points={points('latency_ms', maxLatency)} className="line-latency" /><text x={left} y={203}>0 s</text><text x={right} y={203} textAnchor="end">{samples.at(-1).second} s</text></>}</svg>{!samples.length && <div className="chart-empty"><Activity size={27} /><span>Tu próxima ráfaga, en tiempo real.</span><small>Iniciá un escenario para ver las métricas.</small></div>}</div>;
}

function RunResult({ run }) {
  const checkNames = { catalog_available: 'Catálogo disponible durante toda la prueba', all_requests_finished: 'Todas las solicitudes terminaron sin errores de transporte', http_429_observed: 'Se observaron rechazos HTTP 429', concurrency_bounded: 'Máximo de 3 pagos simultáneos', queue_bounded: 'Cola de hasta 5 operaciones', overload_observed: 'Se observaron rechazos HTTP 503', concurrency_exceeds_three: 'Sin protección: se superaron los 3 pagos activos' };
  return <div className="panel run-result"><div className="section-heading"><h2>{run ? { rate: 'Frecuencia bajo control', bulkhead: 'Pagos aislados', backpressure: 'El cliente regula el ritmo', unprotected: 'Sin límites de pagos' }[run.scenario] : 'Resultados que se pueden comprobar.'}</h2><span className="outline-tag">TEMA 14</span></div>{run ? <><div className="progress-label"><span>{run.status === 'running' ? 'Procesando solicitudes' : run.status === 'failed' ? 'La prueba encontró un error' : 'Ejecución finalizada'}</span><b>{run.completed} / {run.count}</b></div><div className="progress"><span style={{ width: `${run.completed / run.count * 100}%` }} /></div><div className="run-stats"><div><b>{run.confirmed}</b><span>confirmadas</span></div><div><b>{run.rejected}</b><span>rechazos finales</span></div><div><b>{run.retries}</b><span>reintentos</span></div><div><b>{run.p95_ms ? `${(run.p95_ms / 1000).toFixed(1)} s` : '—'}</b><span>p95 de compra</span></div></div>{run.error && <p role="alert" className="error-text">{run.error}</p>}<div className="checks">{Object.entries(run.checks).map(([key, pass]) => <span key={key} className={pass ? 'pass' : 'fail'}>{pass ? <CheckCircle2 size={15} /> : <XCircle size={15} />}{checkNames[key]}</span>)}</div><p className="run-footnote">{run.attempts} intentos HTTP · {run.errors} errores de transporte · Pico observado: {run.peak_active} activos / {run.peak_waiting} en cola. Los contadores HTTP incluyen reintentos.</p></> : <p className="muted">Al terminar, vas a ver las compras confirmadas, los rechazos y las verificaciones automáticas de cada protección.</p>}</div>;
}

function Concept({ number, title, subtitle, text }) {
  return <article className="concept"><span>{number}</span><h3>{title}</h3><b>{subtitle}</b><p>{text}</p></article>;
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
