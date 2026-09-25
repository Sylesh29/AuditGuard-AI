/* AuditGuard AI frontend. No build step: React + htm tagged templates (vendored). */
(function () {
  'use strict';

  const { useState, useEffect, useRef, Fragment } = React;
  const html = htm.bind(React.createElement);
  const API = window.AUDITGUARD_API;

  /* ─────────────── ICONS ─────────────── */
  const svg = (children, strokeWidth = 2.5) => html`
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width=${strokeWidth} stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      ${children}
    </svg>`;
  const IconSearch = () => svg(html`<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>`);
  const IconChart = () => svg(html`<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>`);
  const IconWrench = () => svg(html`<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>`);
  const IconDoc = () => svg(html`<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>`);
  const IconCheck = () => svg(html`<polyline points="20 6 9 17 4 12"/>`, 3);

  const STAGES = [
    { key: 'scout',    label: 'Scout',    desc: 'Finds issues',       Icon: IconSearch,
      working: 'Scanning for duplicates, conflicting lots, unit conflicts, outliers, dates and spec contradictions…' },
    { key: 'ranker',   label: 'Ranker',   desc: 'Ranks by risk',      Icon: IconChart,
      working: 'Ranking by audit risk with fixed rules, then requesting an advisory review…' },
    { key: 'fixer',    label: 'Fixer',    desc: 'Proposes fixes',     Icon: IconWrench,
      working: 'Proposing safe corrections, flagging and escalating the rest. Source data is not modified…' },
    { key: 'narrator', label: 'Narrator', desc: 'Writes the report',  Icon: IconDoc,
      working: 'Building the report and PDF…' },
  ];
  const IDLE_STAGES = { scout: 'pending', ranker: 'pending', fixer: 'pending', narrator: 'pending' };

  const SEVERITY_WORD = { HIGH: 'Critical', MED: 'Moderate', LOW: 'Minor' };
  const ACTION = {
    corrected: { cls: 'fixed', label: 'Fix proposed' },
    flagged:   { cls: 'flagged', label: 'Flagged' },
    escalated: { cls: 'escalated', label: 'Escalated' },
  };
  const STATUS_TAG = {
    pending: 'Waiting', running: 'Working…', complete: 'Done ✓', error: 'Error', skipped: 'Skipped',
  };

  const now = () => new Date().toLocaleTimeString('en-US',
    { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const SUMMARY_LABELS = {
    total: 'findings', HIGH: 'critical', MED: 'moderate', LOW: 'minor', rows_scanned: 'rows scanned',
    ranked: 'ranked', review: 'review', corrected: 'fixes proposed', flagged: 'flagged',
    escalated: 'escalated', changes_logged: 'changes logged', findings_in_report: 'in report',
    summary: 'summary',
  };
  const describe = (summary) => Object.entries(summary || {})
    .map(([k, v]) => (typeof v === 'number' ? `${v} ${SUMMARY_LABELS[k] || k}` : `${SUMMARY_LABELS[k] || k}: ${v}`))
    .join(' · ');

  async function apiError(res) {
    try { return (await res.json()).detail || `HTTP ${res.status}`; }
    catch (_) { return `HTTP ${res.status}`; }
  }

  /* ─────────────── UPLOAD ─────────────── */
  function UploadZone({ onUpload, disabled }) {
    const [drag, setDrag] = useState(false);
    const [file, setFile] = useState(null);

    const handle = (f) => {
      if (!f || disabled) return;
      if (!f.name.toLowerCase().endsWith('.csv')) { alert('Please upload a CSV file.'); return; }
      setFile(f.name);
      onUpload(f);
    };

    return html`
      <label
        className=${`upload-zone${drag ? ' drag' : ''}${disabled ? ' disabled' : ''}${file ? ' has-file' : ''}`}
        onDrop=${(e) => { e.preventDefault(); setDrag(false); handle(e.dataTransfer.files[0]); }}
        onDragOver=${(e) => { e.preventDefault(); if (!disabled) setDrag(true); }}
        onDragLeave=${() => setDrag(false)}
      >
        <input type="file" className="upload-file-input" accept=".csv,text/csv"
          aria-label="Choose a CSV file to audit"
          onChange=${(e) => { handle(e.target.files[0]); e.target.value = ''; }} disabled=${disabled} />
        <div className="upload-icon-ring" aria-hidden="true">${file ? '✓' : '↑'}</div>
        ${file ? html`
          <div className="upload-h">Dataset received</div>
          <div className="upload-sub" style=${{ marginBottom: 14 }}>Drop another file to start a new audit</div>
          <div className="file-chip">📄 ${file}</div>
        ` : html`
          <div className="upload-h">Drop your manufacturing CSV</div>
          <div className="upload-sub">Or click to browse · a lot_number column is required</div>
          <div className="upload-pills">
            <span className="upload-pill">🔍 Scout detects issues</span>
            <span className="upload-pill">⚡ Ranker prioritizes risk</span>
            <span className="upload-pill">🔧 Fixer proposes fixes</span>
            <span className="upload-pill">📄 Narrator writes the PDF</span>
          </div>
        `}
      </label>`;
  }

  /* ─────────────── PIPELINE ─────────────── */
  function Pipeline({ stages, summaries, runStatus }) {
    const connector = (a, b) => {
      if (stages[a] === 'complete' && stages[b] === 'complete') return 'done';
      if (stages[a] === 'complete' && stages[b] === 'running') return 'flowing';
      return 'idle';
    };
    const cls = (st) => (st === 'pending' || st === 'skipped' ? 'idle' : st);

    return html`
      <div className="pipeline-card section">
        <div className="pipeline-track">
          ${STAGES.map((st, i) => html`
            <${Fragment} key=${st.key}>
              <div className="p-node-wrap">
                <div className=${`p-node ${cls(stages[st.key])}`}>
                  ${stages[st.key] === 'complete' ? html`<${IconCheck} />` : html`<${st.Icon} />`}
                  <span className="p-step-badge">${i + 1}</span>
                </div>
                <div className="p-label">
                  <span className="p-name">${st.label}</span>
                  <span className="p-desc">${st.desc}</span>
                  <span className=${`p-status-tag ${cls(stages[st.key])}`}>${STATUS_TAG[stages[st.key]]}</span>
                  ${summaries[st.key] && html`<div className="p-stats">${describe(summaries[st.key])}</div>`}
                </div>
              </div>
              ${i < STAGES.length - 1 && html`
                <div className="p-connector">
                  <div className=${`p-line ${connector(st.key, STAGES[i + 1].key)}`} />
                </div>`}
            </${Fragment}>`)}
        </div>
        ${runStatus === 'complete' && html`
          <div className="pipeline-footer">
            <span className="footer-dot" />
            All 4 stages completed · The source file was not modified · Report ready
          </div>`}
      </div>`;
  }

  /* ─────────────── LIVE LOG ─────────────── */
  function LiveLog({ logs, running }) {
    const endRef = useRef(null);
    useEffect(() => { endRef.current && endRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [logs]);
    return html`
      <div className="log-panel section" role="log" aria-live="polite">
        <div className="log-header">AuditGuard AI · Live pipeline output</div>
        ${logs.map((l, i) => html`
          <div className="log-row" key=${i}>
            <span className="log-ts">${l.ts}</span>
            <span className=${`log-src ${l.agent}`}>[${l.agent.toUpperCase()}]</span>
            <span className="log-text">${l.msg}</span>
          </div>`)}
        ${running && html`
          <div className="log-row">
            <span className="log-ts">—</span>
            <span className="log-src system">[SYS]</span>
            <span className="log-text">Processing<span className="log-cursor" /></span>
          </div>`}
        <div ref=${endRef} />
      </div>`;
  }

  /* ─────────────── STATS ─────────────── */
  function StatsRow({ stats }) {
    const cards = [
      { k: 'HIGH',      label: 'Critical',      cls: 's-crit',  ico: '🚨' },
      { k: 'MED',       label: 'Moderate',      cls: 's-mod',   ico: '⚠️' },
      { k: 'LOW',       label: 'Minor',         cls: 's-low',   ico: '📋' },
      { k: 'corrected', label: 'Fixes proposed', cls: 's-fixed', ico: '✅' },
      { k: 'escalated', label: 'Need sign-off', cls: 's-escal', ico: '🔴' },
    ];
    return html`
      <div className="stats-grid">
        ${cards.map(({ k, label, cls, ico }, i) => html`
          <div key=${k} className=${`stat-card ${cls}`} style=${{ animationDelay: `${i * 70}ms` }}>
            <div className="stat-ico" aria-hidden="true">${ico}</div>
            <div className="stat-num">${stats[k] ?? 0}</div>
            <div className="stat-lbl">${label}</div>
          </div>`)}
      </div>`;
  }

  /* ─────────────── FINDINGS ─────────────── */
  function FindingCard({ f, delay }) {
    const [open, setOpen] = useState(false);
    const action = ACTION[f.action] || { cls: 'none', label: 'No action' };
    const toggle = () => setOpen(!open);

    return html`
      <div className=${`f-card${open ? ' open' : ''}`} style=${{ animationDelay: `${delay}ms` }}>
        <div className="f-bar">
          <div className=${`f-strip ${f.severity}`} />
          <div className="f-main" role="button" tabIndex="0" aria-expanded=${open}
            onClick=${toggle} onKeyDown=${(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } }}>
            <div className="f-rank-col">
              <span className="f-rank-num">#${f.rank}</span>
              <span className="f-rank-lbl">rank</span>
            </div>
            <div className="f-body-col">
              <div className="f-meta">
                <span className="f-type">${f.issue_label}</span>
                <span className="f-id">${f.finding_id}</span>
                <span className=${`sev-pill ${f.severity}`}>${SEVERITY_WORD[f.severity]}</span>
              </div>
              <div className="f-preview">${f.reason}</div>
            </div>
            <div className="f-right">
              <span className=${`act-badge ${action.cls}`}>${action.label}</span>
              <span className="f-rows">${f.rows_affected} row${f.rows_affected !== 1 ? 's' : ''}</span>
              <span className="f-chevron" aria-hidden="true">▼</span>
            </div>
          </div>
        </div>
        ${open && html`
          <div className="f-expand">
            <div className="f-expand-grid">
              <div className="f-expand-block">
                <div className="f-expand-lbl">Why it is ranked here</div>
                <div className="f-expand-val">${f.ranking_reason}</div>
                <div className="f-expand-lbl" style=${{ marginTop: 10 }}>Regulatory reference</div>
                <div className="f-expand-val">${f.regulatory_reference}</div>
              </div>
              <div className="f-expand-block">
                <div className="f-expand-lbl">Action</div>
                <div className="f-expand-val">${f.action_description || 'No action taken'}</div>
                ${f.action_reason && html`<div className="f-expand-val">${f.action_reason}</div>`}
                ${f.review_suggestion && html`
                  <div className="f-expand-lbl" style=${{ marginTop: 10 }}>Reviewer suggestion (advisory)</div>
                  <div className="f-expand-val ai-note">
                    Suggests rank #${f.review_suggestion.suggested_rank}: ${f.review_suggestion.reason}
                  </div>`}
                <div className="f-expand-lbl" style=${{ marginTop: 10 }}>Lots · CSV lines</div>
                <div className="f-lots">${f.lot_numbers.join(', ')} · line ${f.csv_lines.join(', ')}</div>
              </div>
            </div>
          </div>`}
      </div>`;
  }

  function FindingsSection({ findings }) {
    const [filter, setFilter] = useState('ALL');
    const tabs = [
      { k: 'ALL', label: 'All', cls: 't-all' },
      { k: 'HIGH', label: 'Critical', cls: 't-high' },
      { k: 'MED', label: 'Moderate', cls: 't-med' },
      { k: 'LOW', label: 'Minor', cls: 't-low' },
    ];
    const count = (k) => (k === 'ALL' ? findings.length : findings.filter((f) => f.severity === k).length);
    const visible = filter === 'ALL' ? findings : findings.filter((f) => f.severity === filter);

    return html`
      <div className="section">
        <div className="findings-hd">
          <div>
            <span className="findings-title">Findings</span>${'  '}
            <span className="findings-sub">sorted by audit risk · click a row to expand</span>
          </div>
          <div className="filter-bar" role="tablist">
            ${tabs.map(({ k, label, cls }) => html`
              <button key=${k} role="tab" aria-selected=${filter === k}
                className=${`f-tab ${cls}${filter === k ? ' on' : ''}`} onClick=${() => setFilter(k)}>
                ${label}<span className="f-count">${count(k)}</span>
              </button>`)}
          </div>
        </div>
        <div className="findings-list">
          ${visible.map((f, i) => html`<${FindingCard} key=${f.finding_id} f=${f} delay=${Math.min(i, 20) * 40} />`)}
        </div>
      </div>`;
  }

  /* ─────────────── DOWNLOADS ─────────────── */
  function DownloadBanner({ runId, referenceId }) {
    const href = (name) => `${API}/api/runs/${runId}/${name}`;
    return html`
      <div className="download-banner section">
        <div className="dl-ready-pill">● Report ready · ${referenceId}</div>
        <div className="dl-title">Your Report is Ready to Review and Sign</div>
        <div className="dl-sub">
          Every finding, every proposed correction with its original value, and every open item,
          with a certification block and signature lines.
        </div>
        <div className="dl-actions">
          <a className="dl-btn" href=${href('report.pdf')} download><span>↓</span> Audit report (PDF)</a>
          <a className="dl-btn secondary" href=${href('corrected.csv')} download>Proposed corrected data (CSV)</a>
          <a className="dl-btn secondary" href=${href('changelog.csv')} download>Change log (CSV)</a>
        </div>
        <div className="dl-meta">The uploaded file is never modified · corrections are proposals for your change-control process</div>
      </div>`;
  }

  /* ─────────────── APP ─────────────── */
  function App() {
    const [stages, setStages] = useState(IDLE_STAGES);
    const [summaries, setSummaries] = useState({});
    const [runStatus, setRunStatus] = useState('idle'); // idle | uploading | running | complete | failed
    const [result, setResult] = useState(null);
    const [run, setRun] = useState(null);
    const [error, setError] = useState(null);
    const [logs, setLogs] = useState([]);
    const [health, setHealth] = useState(null);
    const sourceRef = useRef(null);

    useEffect(() => {
      fetch(`${API}/api/health`).then((r) => r.json()).then(setHealth).catch(() => setHealth(false));
      return () => sourceRef.current && sourceRef.current.close();
    }, []);

    const log = (agent, msg) => setLogs((prev) => [...prev, { ts: now(), agent, msg }]);

    async function loadResults(runId) {
      const res = await fetch(`${API}/api/runs/${runId}/findings`);
      if (!res.ok) throw new Error(await apiError(res));
      const data = await res.json();
      setResult(data);
      log('system', `${data.findings.length} findings loaded · report ready for download`);
    }

    function handleEvent(ev, runId) {
      if (ev.type === 'stage') {
        setStages((prev) => ({ ...prev, [ev.stage]: ev.status }));
        const stage = STAGES.find((s) => s.key === ev.stage);
        if (ev.status === 'running') log(ev.stage, stage.working);
        if (ev.status === 'complete') {
          setSummaries((prev) => ({ ...prev, [ev.stage]: ev.summary }));
          log(ev.stage, `Done: ${describe(ev.summary)}`);
        }
        if (ev.status === 'error') log(ev.stage, `ERROR: ${ev.message}`);
        if (ev.status === 'skipped') log(ev.stage, 'Skipped because an earlier stage failed');
      } else if (ev.type === 'run') {
        sourceRef.current && sourceRef.current.close();
        setRunStatus(ev.status);
        if (ev.status === 'complete') {
          loadResults(runId).catch((e) => setError(e.message));
        } else {
          setError(ev.message || 'The audit failed.');
          log('system', `Audit failed: ${ev.message}`);
        }
      }
    }

    async function handleUpload(file) {
      sourceRef.current && sourceRef.current.close();
      setRunStatus('uploading'); setStages(IDLE_STAGES); setSummaries({});
      setResult(null); setRun(null); setError(null); setLogs([]);
      log('system', `Uploading ${file.name} (${(file.size / 1024).toFixed(1)} KB)`);

      const body = new FormData();
      body.append('file', file);
      let created;
      try {
        const res = await fetch(`${API}/api/runs`, { method: 'POST', body });
        if (!res.ok) throw new Error(await apiError(res));
        created = await res.json();
      } catch (e) {
        setRunStatus('failed'); setError(e.message); log('system', `Upload rejected: ${e.message}`);
        return;
      }

      setRun(created); setRunStatus('running');
      log('system', `Audit ${created.reference_id} started`);
      // EventSource reconnects on its own and resumes with Last-Event-ID.
      const source = new EventSource(`${API}${created.events_url}`);
      sourceRef.current = source;
      source.onmessage = (msg) => {
        let ev;
        try { ev = JSON.parse(msg.data); } catch (e) {
          console.error('Malformed event', msg.data, e);
          return;
        }
        handleEvent(ev, created.run_id);
      };
      source.onerror = () => { if (source.readyState === EventSource.CONNECTING) log('system', 'Connection lost, reconnecting…'); };
    }

    const busy = runStatus === 'uploading' || runStatus === 'running';

    return html`
      <div className="app-shell">
        <header className="header">
          <div className="header-brand">
            <div className="brand-shield" aria-hidden="true">🛡️</div>
            <div>
              <div className="brand-name">AuditGuard AI</div>
              <div className="brand-sub">Manufacturing data integrity</div>
            </div>
          </div>
          <div className="header-right">
            <div className="conn-pill">
              <span className=${`conn-dot${health ? '' : ' off'}`} />
              ${health ? (health.llm_enabled ? 'API connected' : 'API connected · LLM off') : 'API offline'}
            </div>
          </div>
        </header>

        <main>
          <div className="page-content">
            ${runStatus === 'idle' && html`
              <div className="intro">
                <div className="intro-eyebrow">Pre-inspection data triage</div>
                <div className="intro-h">Audit-Ready in Minutes,<br />Not Days</div>
                <div className="intro-p">
                  Upload a manufacturing CSV. AuditGuard finds data-integrity issues with fixed,
                  explainable rules, ranks them by audit risk, proposes safe corrections without
                  touching your original file, and gives you a report to review and sign.
                </div>
              </div>`}

            <${UploadZone} onUpload=${handleUpload} disabled=${busy} />
            ${error && html`<div className="err-bar" role="alert">⚠ ${error}</div>`}
            ${run && html`<${Pipeline} stages=${stages} summaries=${summaries} runStatus=${runStatus} />`}
            ${logs.length > 0 && html`<${LiveLog} logs=${logs} running=${busy} />`}

            ${result && html`
              <div className="section">
                <div className="section-hd">
                  <span className="section-hd-title">Audit summary</span>
                  <div className="section-rule" />
                </div>
                <${StatsRow} stats=${result.stats} />
              </div>
              ${result.findings.length > 0 && html`<${FindingsSection} findings=${result.findings} />`}
              <${DownloadBanner} runId=${result.run_id} referenceId=${result.reference_id} />`}
          </div>
        </main>
      </div>`;
  }

  ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
})();
