import { useState } from 'react';
import {
  Activity,
  ArrowRight,
  Bell,
  Check,
  ChevronDown,
  CircleHelp,
  CloudUpload,
  FileAudio,
  FolderOpen,
  Headphones,
  LayoutDashboard,
  Library,
  Menu,
  MoreHorizontal,
  Music2,
  Play,
  Plus,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Upload,
  X,
  Zap,
} from 'lucide-react';
import './styles.css';
import { analyzeUploadedFile, createOptimizedMidi, getRepairPreview } from './musicAnalysis';

const navItems = [
  { label: 'Overview', icon: LayoutDashboard, active: true },
  { label: 'My projects', icon: Library, count: '12' },
  { label: 'Sound library', icon: Headphones },
];

const recentProjects = [
  { title: 'Lepa Brena — Čik pogodi', type: 'PA800 Style', time: 'Edited 18 min ago', score: 96, color: 'coral', icon: '♫' },
  { title: 'Live set / 2024', type: 'MIDI Collection', time: 'Edited yesterday', score: 88, color: 'violet', icon: '◒' },
  { title: 'Balkan Groove Vol. 2', type: 'Style Pack', time: 'Edited 3 days ago', score: 74, color: 'amber', icon: '♬' },
];

const REPAIR_PRESETS = [
  { key: 'pa800-safe', name: 'PA800 Safe', detail: 'Preserve structure', tone: 'purple' },
  { key: 'stage-ready', name: 'Stage Ready', detail: 'Balanced dynamics', tone: 'mint' },
  { key: 'cleaner-groove', name: 'Cleaner Groove', detail: 'Tighter velocity', tone: 'amber' },
  { key: 'more-expression', name: 'More Expression', detail: 'Keep the feel', tone: 'coral' },
];

const checks = [
  { label: 'Chord recognition', detail: 'All 24 patterns mapped', status: 'Passed', tone: 'success' },
  { label: 'Velocity consistency', detail: '3 expressive peaks found', status: 'Review', tone: 'warning' },
  { label: 'Style structure', detail: 'Intro, fills and endings', status: 'Passed', tone: 'success' },
  { label: 'MIDI compatibility', detail: 'PA800 profile detected', status: 'Passed', tone: 'success' },
];

function App() {
  const [activeNav, setActiveNav] = useState('Overview');
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [toast, setToast] = useState('');

  const showToast = (message) => {
    setToast(message);
    window.setTimeout(() => setToast(''), 2600);
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark"><Music2 size={18} strokeWidth={2.4} /></div>
          <span>dna<span className="brand-dot">.</span></span>
        </div>

        <div className="workspace-switcher">
          <div className="workspace-avatar">B</div>
          <div className="workspace-copy">
            <span className="workspace-label">Workspace</span>
            <strong>Baja Studio</strong>
          </div>
          <ChevronDown size={15} />
        </div>

        <nav className="primary-nav" aria-label="Main navigation">
          <span className="nav-caption">Workspace</span>
          {navItems.map(({ label, icon: Icon, active, count }) => (
            <button
              className={`nav-item ${activeNav === label ? 'nav-item-active' : ''}`}
              key={label}
              onClick={() => setActiveNav(label)}
            >
              <Icon size={18} />
              <span>{label}</span>
              {count && <span className="nav-count">{count}</span>}
            </button>
          ))}
          <button className="nav-item" onClick={() => showToast('Collections are coming soon')}>
            <FolderOpen size={18} />
            <span>Collections</span>
          </button>
        </nav>

        <div className="sidebar-bottom">
          <div className="upgrade-card">
            <div className="upgrade-icon"><Sparkles size={16} /></div>
            <strong>Unlock Studio Pro</strong>
            <p>Unlimited exports and deeper repairs.</p>
            <button onClick={() => showToast('Studio Pro preview opened')}>Explore Pro <ArrowRight size={14} /></button>
          </div>
          <button className="nav-item" onClick={() => showToast('Settings opened')}><Settings size={18} /><span>Settings</span></button>
          <button className="nav-item" onClick={() => showToast('Help center opened')}><CircleHelp size={18} /><span>Help center</span></button>
          <div className="profile-row">
            <div className="profile-avatar">BB</div>
            <div className="profile-copy"><strong>Baja Beg</strong><span>Free plan</span></div>
            <MoreHorizontal size={18} />
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <button className="mobile-menu" aria-label="Open menu"><Menu size={21} /></button>
          <div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-divider">/</span><strong>{activeNav}</strong></div>
          <div className="topbar-actions">
            <button className="icon-button" aria-label="Search" onClick={() => showToast('Search is ready')}><Search size={19} /></button>
            <button className="icon-button notification-button" aria-label="Notifications" onClick={() => showToast('You are all caught up')}><Bell size={19} /><span className="notification-dot" /></button>
            <button className="new-project-button" onClick={() => setIsUploadOpen(true)}><Plus size={17} /> <span>New project</span></button>
          </div>
        </header>

        <div className="content-wrap">
          <section className="welcome-row">
            <div>
              <p className="eyebrow">Tuesday, September 24, 2024</p>
              <h1>Good afternoon, Baja <span className="wave-mark">✦</span></h1>
              <p className="welcome-copy">Make every note work harder. Your studio is ready.</p>
            </div>
            <div className="date-chip"><Activity size={16} /><span>Studio health</span><strong>86%</strong></div>
          </section>

          <section className="hero-grid">
            <div className="hero-card">
              <div className="hero-glow" />
              <div className="hero-card-content">
                <div className="hero-kicker"><span className="live-pulse" /> QUICK OPTIMIZER</div>
                <h2>Clean up your next<br /><em>performance.</em></h2>
                <p>Drop in a MIDI or style file. DNA finds the friction and gives you a stage-ready version in minutes.</p>
                <button className="primary-button" onClick={() => setIsUploadOpen(true)}>Start optimizing <ArrowRight size={16} /></button>
              </div>
              <div className="waveform" aria-hidden="true">
                {['18', '34', '26', '54', '42', '76', '38', '62', '28', '88', '47', '67', '36', '58', '22', '44', '30', '70', '50', '38', '62', '28', '76', '42', '54', '32', '68', '44', '26', '58', '36', '72', '48', '30', '66', '40'].map((height, index) => <i className={`wave-bar wave-bar-${height}`} key={index} />)}
              </div>
              <div className="hero-status"><span><span className="status-dot" /> MIDI engine online</span><span>v2.4.1</span></div>
            </div>
            <div className="score-card">
              <div className="section-heading"><div><span className="card-label">LATEST ANALYSIS</span><h3>Live set / 2024</h3></div><button className="more-button" aria-label="More options"><MoreHorizontal size={19} /></button></div>
              <div className="score-ring-wrap"><div className="score-ring"><div className="score-ring-inner"><strong>88</strong><span>/ 100</span></div></div><div className="score-summary"><span className="score-trend"><Zap size={14} /> +12 pts</span><p>Cleaner dynamics<br />than last version</p></div></div>
              <div className="score-divider" />
              <div className="metric-row"><span>Notes in place</span><strong>94%</strong><div className="metric-track"><i className="metric-fill fill-green fill-94" /></div></div>
              <div className="metric-row"><span>Expression range</span><strong>81%</strong><div className="metric-track"><i className="metric-fill fill-purple fill-81" /></div></div>
              <button className="text-button" onClick={() => showToast('Opening Live set / 2024')}>View full analysis <ArrowRight size={15} /></button>
            </div>
          </section>

          <section className="section-block">
            <div className="section-heading projects-heading"><div><span className="card-label">YOUR WORKSPACE</span><h2>Recent projects</h2></div><button className="view-all-button" onClick={() => setActiveNav('My projects')}>View all <ArrowRight size={15} /></button></div>
            <div className="project-grid">
              {recentProjects.map((project) => <ProjectCard key={project.title} project={project} onOpen={() => showToast(`${project.title} opened`)} />)}
              <button className="add-project-card" onClick={() => setIsUploadOpen(true)}><span className="add-icon"><Plus size={20} /></span><strong>Start a new project</strong><span>Import MIDI, MP3 or style</span></button>
            </div>
          </section>

          <section className="section-block workflow-section">
            <div className="section-heading projects-heading"><div><span className="card-label">HOW IT WORKS</span><h2>From rough to ready</h2></div><button className="view-all-button" onClick={() => showToast('Workflow guide opened')}>See the guide <ArrowRight size={15} /></button></div>
            <div className="workflow-card">
              <div className="workflow-step"><div className="step-number step-done"><Check size={16} /></div><div><strong>Analyze</strong><span>Map every part of your performance.</span></div></div>
              <div className="step-connector" />
              <div className="workflow-step"><div className="step-number step-current">2</div><div><strong>Repair</strong><span>Fix timing, dynamics and structure.</span></div></div>
              <div className="step-connector" />
              <div className="workflow-step"><div className="step-number step-next">3</div><div><strong>Export</strong><span>Send a clean file to your keyboard.</span></div></div>
              <button className="workflow-play" aria-label={isPlaying ? 'Pause demo' : 'Play demo'} onClick={() => setIsPlaying(!isPlaying)}>{isPlaying ? <span className="pause-icon">Ⅱ</span> : <Play size={15} fill="currentColor" />}<span>{isPlaying ? 'Playing demo' : 'Play 30 sec demo'}</span></button>
            </div>
          </section>

          <section className="section-block checks-section">
            <div className="section-heading projects-heading"><div><span className="card-label">LAST RUN</span><h2>Live set / 2024 checks</h2></div><button className="filter-button" onClick={() => showToast('Filters opened')}><SlidersHorizontal size={15} /> Filter</button></div>
            <div className="checks-table">{checks.map((check) => <div className="check-row" key={check.label}><div className={`check-status-icon ${check.tone}`}><Check size={15} /></div><div className="check-copy"><strong>{check.label}</strong><span>{check.detail}</span></div><span className={`check-badge ${check.tone}`}>{check.status}</span><button className="row-arrow" onClick={() => showToast(`${check.label} details opened`)}><ArrowRight size={16} /></button></div>)}</div>
          </section>
        </div>
      </main>

      {isUploadOpen && <OptimizerModal onClose={() => setIsUploadOpen(false)} onToast={showToast} />}
      {toast && <div className="toast"><Check size={16} />{toast}</div>}
    </div>
  );
}

function OptimizerModal({ onClose, onToast }) {
  const [stage, setStage] = useState('upload');
  const [selectedFile, setSelectedFile] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [analysisError, setAnalysisError] = useState('');
  const [presetKey, setPresetKey] = useState('pa800-safe');
  const [previewMode, setPreviewMode] = useState('optimized');

  const chooseFile = (file) => {
    if (!file) return;
    setSelectedFile(file);
    setAnalysisError('');
    setStage('ready');
  };

  const runAnalysis = async () => {
    if (!selectedFile) return;
    setStage('analyzing');
    setAnalysisError('');
    try {
      const result = await analyzeUploadedFile(selectedFile);
      setAnalysis({ ...result, preset: REPAIR_PRESETS.find((preset) => preset.key === presetKey), preview: getRepairPreview(result, presetKey) });
      setStage('results');
    } catch (error) {
      setAnalysisError(error.message);
      setStage('ready');
    }
  };

  const previewData = analysis?.preview?.[previewMode];

  const exportRepair = async () => {
    const { blob, repairedNotes } = await createOptimizedMidi(selectedFile, presetKey);
    const downloadUrl = URL.createObjectURL(blob);
    const downloadLink = document.createElement('a');
    downloadLink.href = downloadUrl;
    downloadLink.download = `${selectedFile.name.replace(/\.[^/.]+$/, '')}.optimized.mid`;
    document.body.appendChild(downloadLink);
    downloadLink.click();
    downloadLink.remove();
    URL.revokeObjectURL(downloadUrl);
    onToast(`${repairedNotes} notes normalized and exported`);
    onClose();
  };

  return <div className="modal-backdrop" onClick={stage === 'analyzing' ? undefined : onClose}>
    <div className="upload-modal" onClick={(event) => event.stopPropagation()}>
      {stage !== 'analyzing' && <button className="modal-close" onClick={onClose} aria-label="Close"><X size={18} /></button>}
      <div className="modal-icon">{stage === 'results' ? <Check size={24} /> : <CloudUpload size={24} />}</div>
      <span className="card-label">{stage === 'results' ? 'ANALYSIS COMPLETE' : 'NEW PROJECT'}</span>
      <h2>{stage === 'upload' && 'Bring in your performance'}{stage === 'ready' && 'Ready to optimize'}{stage === 'analyzing' && 'Reading your performance'}{stage === 'results' && 'Your stage-ready version'}</h2>
      {stage === 'upload' && <>
        <p>Upload a MIDI, MP3 or Korg style file and start with a clean analysis.</p>
        <label className="dropzone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); chooseFile(event.dataTransfer.files[0]); }}>
          <input className="file-input-hidden" type="file" accept=".mid,.midi,.kar,.sty,.mp3" onChange={(event) => chooseFile(event.target.files[0])} />
          <Upload size={20} /><strong>Choose a file</strong><span>or drag and drop it here</span>
        </label>
        <div className="supported-formats"><FileAudio size={15} /> MIDI, MP3, KAR, STY up to 250 MB</div>
      </>}
      {stage === 'ready' && <>
        <p className="selected-file-copy">We’ll scan the arrangement and prepare safe repair suggestions.</p>
        <div className="selected-file"><div className="selected-file-icon"><FileAudio size={18} /></div><div><strong>{selectedFile?.name}</strong><span>{Math.ceil((selectedFile?.size ?? 0) / 1024)} KB · Ready for analysis</span></div><button onClick={() => { setSelectedFile(null); setAnalysisError(''); setStage('upload'); }} aria-label="Choose another file"><X size={16} /></button></div>
        {analysisError && <div className="analysis-error" role="alert"><X size={14} />{analysisError}</div>}
        <div className="preset-heading"><span>Choose a repair profile</span><strong>1 click</strong></div>
        <div className="preset-grid">{REPAIR_PRESETS.map((preset) => <button className={`preset-option ${presetKey === preset.key ? 'preset-selected' : ''} preset-${preset.tone}`} key={preset.key} onClick={() => setPresetKey(preset.key)}><span className="preset-radio">{presetKey === preset.key && <Check size={11} />}</span><span><strong>{preset.name}</strong><small>{preset.detail}</small></span></button>)}</div>
        <button className="modal-primary-button" onClick={runAnalysis}>Analyze file <ArrowRight size={16} /></button>
      </>}
      {stage === 'analyzing' && <div className="analysis-progress" aria-live="polite"><div className="analysis-spinner"><Activity size={22} /></div><strong>Mapping notes, chords and dynamics</strong><span>Checking PA800 compatibility profile</span><div className="progress-track"><i /></div></div>}
      {stage === 'results' && <>
        <p className="selected-file-copy">Analysis complete for <strong>{analysis?.fileName}</strong>.</p>
        <div className="preview-switcher" role="tablist" aria-label="Compare repair versions"><button className={previewMode === 'original' ? 'preview-tab preview-tab-active' : 'preview-tab'} onClick={() => setPreviewMode('original')} role="tab" aria-selected={previewMode === 'original'}>Original</button><button className={previewMode === 'optimized' ? 'preview-tab preview-tab-active' : 'preview-tab'} onClick={() => setPreviewMode('optimized')} role="tab" aria-selected={previewMode === 'optimized'}>Optimized</button></div>
        <div className="preview-metrics"><div className="preview-score"><span>{previewMode === 'optimized' ? 'Optimized score' : 'Original score'}</span><strong>{previewData?.score}</strong><small>/ 100</small></div><div><span>Average velocity</span><strong>{previewData?.averageVelocity}</strong></div><div><span>Velocity range</span><strong>{previewData?.velocitySpread}</strong></div></div>
        <div className="analysis-score-line"><strong>{previewData?.score}</strong><span>/ 100 arrangement score</span><small>{analysis?.preset?.name} · {analysis?.formatLabel}</small></div>
        <div className="repair-results"><div><Check size={15} /><span>Timing confidence</span><strong>{analysis?.timingScore}%</strong></div><div><Check size={15} /><span>Expression range</span><strong>{analysis?.expressionScore}%</strong></div><div><Check size={15} /><span>{analysis?.notes.toLocaleString()} notes · {analysis?.channels} channels</span><strong>{analysis?.tempo} BPM</strong></div></div>
        <div className="marker-summary"><span>PA800 style markers</span><strong>{analysis?.styleMarkers.length || 'None detected'}</strong></div>
        {analysis?.styleMarkers.length > 0 && <div className="marker-list">{analysis.styleMarkers.map((marker) => <span key={marker}>{marker}</span>)}</div>}
        <div className="coverage-panel"><div className="coverage-heading"><span>PA800 style coverage</span><strong>{analysis?.styleCoverage.coveredSlots}/{analysis?.styleCoverage.totalSlots} CV slots</strong></div><div className="coverage-grid">{analysis?.styleCoverage.elements.map((element) => <div className={`coverage-item ${element.found === element.chordVariations ? 'coverage-complete' : ''}`} key={element.key}><span>{element.label}</span><strong>{element.found}/{element.chordVariations}</strong></div>)}</div></div>
        <button className="modal-primary-button" onClick={exportRepair}>Apply repair & export <ArrowRight size={16} /></button>
      </>}
    </div>
  </div>;
}

function ProjectCard({ project, onOpen }) {
  return <article className="project-card" onClick={onOpen}><div className={`project-art art-${project.color}`}><span>{project.icon}</span><div className="art-lines"><i /><i /><i /><i /></div><div className="play-overlay"><Play size={15} fill="currentColor" /></div></div><div className="project-card-body"><div className="project-card-top"><div><h3>{project.title}</h3><span>{project.type} · {project.time}</span></div><button className="more-button" onClick={(event) => event.stopPropagation()} aria-label="More options"><MoreHorizontal size={18} /></button></div><div className="project-score"><div className="mini-score-track"><i className={`score-fill score-fill-${project.score}`} /></div><strong>{project.score}</strong><span>health score</span></div></div></article>;
}

export default App;
