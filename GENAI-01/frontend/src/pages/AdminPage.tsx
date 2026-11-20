import { ChangeEvent, FormEvent, ReactNode, useEffect, useState } from 'react';
import {
  AlertCircle, ArrowUpRight, Check, CheckCircle2, Clock3, Database, FileText, FileUp,
  Globe, HardDrive, LoaderCircle, LockKeyhole, RefreshCw, ShieldCheck, UploadCloud,
} from 'lucide-react';
import { ApiError, fetchAdminStats, ingestUrl, uploadDocument } from '../api/client';
import type { AdminStats, IngestResponse, Role } from '../types';

const ROLES: { value: Role; label: string; description: string }[] = [
  { value: 'finance', label: 'Finance', description: 'Financial filings and finance policies' },
  { value: 'engineering', label: 'Engineering', description: 'Technical documentation and engineering policies' },
  { value: 'guest', label: 'Guest', description: 'General, non-confidential knowledge' },
];

interface AdminPageProps {
  role: Role;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

export default function AdminPage({ role }: AdminPageProps) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [mode, setMode] = useState<'file' | 'url'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [allowedRoles, setAllowedRoles] = useState<Role[]>(['finance', 'engineering']);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResponse | null>(null);
  const [dragging, setDragging] = useState(false);

  async function loadStats(): Promise<void> {
    try {
      const summary = await fetchAdminStats();
      setStats(summary);
      setStatsError(null);
    } catch (cause) {
      setStatsError(cause instanceof Error ? cause.message : 'Could not load administration metrics.');
    }
  }

  useEffect(() => {
    if (role === 'admin') void loadStats();
  }, [role]);

  function toggleRole(value: Role): void {
    setAllowedRoles((current) => current.includes(value)
      ? current.filter((item) => item !== value)
      : [...current, value]);
  }

  function acceptFile(candidate: File | undefined): void {
    if (!candidate) return;
    const extension = candidate.name.toLowerCase().split('.').pop();
    if (!['pdf', 'docx'].includes(extension ?? '')) {
      setError('Only PDF and DOCX files are accepted by the ingestion API.');
      setFile(null);
      return;
    }
    if (candidate.size > 25 * 1024 * 1024) {
      setError('The selected file exceeds the 25 MB frontend limit.');
      setFile(null);
      return;
    }
    setError(null);
    setFile(candidate);
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>): void {
    acceptFile(event.target.files?.[0]);
  }

  async function submitIngestion(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setResult(null);
    if (allowedRoles.length === 0) {
      setError('Select at least one audience role. Admin access is added automatically by the backend.');
      return;
    }
    if (mode === 'file' && !file) {
      setError('Choose a PDF or DOCX file to ingest.');
      return;
    }
    if (mode === 'url' && !url.trim()) {
      setError('Enter an HTTP or HTTPS URL to ingest.');
      return;
    }

    setUploading(true);
    try {
      const ingestResult = mode === 'file'
        ? await uploadDocument(file!, allowedRoles)
        : await ingestUrl(url.trim(), allowedRoles);
      setResult(ingestResult);
      setFile(null);
      setUrl('');
      await loadStats();
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : 'Ingestion failed.';
      if (cause instanceof ApiError && cause.status === 413) {
        setError('The server rejected this file because it exceeds the configured upload limit.');
      } else {
        setError(detail);
      }
    } finally {
      setUploading(false);
    }
  }

  if (role !== 'admin') {
    return (
      <div className="access-denied-panel">
        <div className="denied-icon"><LockKeyhole size={25} /></div>
        <span className="eyebrow">RESTRICTED AREA</span>
        <h1>Administrator access required</h1>
        <p>The document ingestion console and aggregate operational metrics are restricted to the <strong>admin</strong> role. Switch to the Admin demo role to inspect this workflow.</p>
        <div className="denied-footnote"><ShieldCheck size={15} /> This restriction is also enforced by the FastAPI backend using JWT claims.</div>
      </div>
    );
  }

  return (
    <div className="admin-page">
      <div className="page-heading-row">
        <div>
          <div className="breadcrumb">ADMINISTRATION <span>/</span> KNOWLEDGE SOURCES</div>
          <h1>Knowledge source <span>management.</span></h1>
          <p className="page-subtitle">Ingest governed documents and control which roles can retrieve their content.</p>
        </div>
        <button className="button button-secondary refresh-button" onClick={() => void loadStats()}>
          <RefreshCw size={15} /> Refresh metrics
        </button>
      </div>

      <div className="admin-security-banner">
        <span><ShieldCheck size={17} /></span>
        <div><strong>Admin-authorized operation</strong><small>Upload and URL ingestion endpoints require a verified admin JWT. The admin role is always retained on ingested records.</small></div>
        <span className="secure-pill">RBAC ENFORCED</span>
      </div>

      <section className="stats-grid" aria-label="Knowledge base metrics">
        <StatCard icon={<FileText size={17} />} label="Documents indexed" value={stats ? formatNumber(stats.documents) : '—'} foot="Distinct source documents" tone="blue" />
        <StatCard icon={<Database size={17} />} label="Knowledge chunks" value={stats ? formatNumber(stats.chunks) : '—'} foot="Retrieval-ready passages" tone="violet" />
        <StatCard icon={<MessageIcon />} label="Queries processed" value={stats ? formatNumber(stats.total_queries) : '—'} foot="Across all access roles" tone="green" />
        <StatCard icon={<Clock3 size={17} />} label="Average latency" value={stats ? `${stats.avg_latency_ms.toFixed(0)} ms` : '—'} foot="End-to-end query time" tone="orange" />
      </section>
      {statsError && <div className="inline-alert admin-alert"><AlertCircle size={15} /> Metrics unavailable: {statsError}</div>}

      <div className="admin-content-grid">
        <section className="panel ingest-panel">
          <div className="panel-heading">
            <div><span className="panel-icon blue"><UploadCloud size={18} /></span><div><h2>Add a knowledge source</h2><p>Upload a local document or ingest an approved public URL.</p></div></div>
            <span className="panel-step">INGEST · 01</span>
          </div>
          <div className="ingest-tabs" role="tablist" aria-label="Ingestion method">
            <button type="button" className={mode === 'file' ? 'active' : ''} onClick={() => setMode('file')} role="tab" aria-selected={mode === 'file'}><FileUp size={15} /> Upload file</button>
            <button type="button" className={mode === 'url' ? 'active' : ''} onClick={() => setMode('url')} role="tab" aria-selected={mode === 'url'}><Globe size={15} /> Import URL</button>
          </div>
          <form onSubmit={(event) => void submitIngestion(event)}>
            {mode === 'file' ? (
              <div
                className={`drop-zone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
                onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => { event.preventDefault(); setDragging(false); acceptFile(event.dataTransfer.files[0]); }}
              >
                <input id="document-file" type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={handleFileChange} />
                {file ? (
                  <><span className="drop-icon success"><CheckCircle2 size={24} /></span><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(2)} MB · Ready to ingest</span><label htmlFor="document-file" className="change-file-link">Choose a different file</label></>
                ) : (
                  <><span className="drop-icon"><UploadCloud size={24} /></span><strong>Drop your document here</strong><span>or <label htmlFor="document-file">browse files</label> from your computer</span><small>PDF or DOCX · Maximum 25 MB</small></>
                )}
              </div>
            ) : (
              <div className="url-field-wrap">
                <label htmlFor="source-url">Public source URL</label>
                <div className="url-input"><Globe size={16} /><input id="source-url" type="url" placeholder="https://docs.python.org/3/…" value={url} onChange={(event) => setUrl(event.target.value)} /></div>
                <small>Use a public HTTP or HTTPS page. Private, local, and reserved network destinations are rejected server-side.</small>
              </div>
            )}

            <div className="permission-section">
              <div className="permission-heading"><div><h3>Retrieval permissions</h3><p>Choose which roles can see this document.</p></div><span className="permission-badge"><LockKeyhole size={12} /> ROLE FILTER</span></div>
              <div className="role-permission-list">
                {ROLES.map((item) => (
                  <label className={`role-permission-option ${allowedRoles.includes(item.value) ? 'selected' : ''}`} key={item.value}>
                    <input type="checkbox" checked={allowedRoles.includes(item.value)} onChange={() => toggleRole(item.value)} />
                    <span className="fake-checkbox"><Check size={12} /></span>
                    <span className="permission-role-text"><strong>{item.label}</strong><small>{item.description}</small></span>
                  </label>
                ))}
                <div className="admin-always-option"><ShieldCheck size={15} /><span><strong>Admin</strong><small>Always retained for governance and document management.</small></span><span className="always-tag">ALWAYS ON</span></div>
              </div>
            </div>

            {error && <div className="inline-alert" role="alert"><AlertCircle size={15} /> {error}</div>}
            <button className="button button-primary ingest-submit" type="submit" disabled={uploading}>
              {uploading ? <LoaderCircle className="spin" size={16} /> : <UploadCloud size={16} />}
              {uploading ? 'Processing source…' : mode === 'file' ? 'Upload & index document' : 'Fetch & index URL'}
            </button>
          </form>
          {result && (
            <div className="ingest-success" role="status">
              <div className="success-heading"><CheckCircle2 size={18} /><strong>Source indexed successfully</strong></div>
              <div className="success-details"><span>{result.source}</span><strong>{result.chunks_created} chunks created</strong></div>
              <div className="success-roles">Accessible to: {result.allowed_roles.map((item) => item.toUpperCase()).join(' · ')}</div>
            </div>
          )}
        </section>

        <aside className="admin-side-column">
          <section className="panel operational-panel">
            <div className="panel-heading compact"><div><span className="panel-icon green"><HardDrive size={17} /></span><div><h2>Operational telemetry</h2><p>Current backend usage snapshot.</p></div></div></div>
            <div className="telemetry-list">
              <TelemetryRow label="Feedback submissions" value={stats ? formatNumber(stats.feedback_count) : '—'} />
              <TelemetryRow label="Estimated model cost" value={stats ? `$${stats.total_estimated_cost_usd.toFixed(4)}` : '—'} />
              <TelemetryRow label="Semantic cache entries" value={stats ? formatNumber(stats.cache_entries) : '—'} />
              <TelemetryRow label="Vector index" value="Qdrant" state="Connected via API" />
            </div>
            {stats && <div className="telemetry-updated">Updated {new Date(stats.generated_at).toLocaleTimeString()}</div>}
          </section>
          <section className="panel ingestion-guide">
            <div className="guide-heading"><span className="panel-icon violet"><Database size={17} /></span><h2>Ingestion lifecycle</h2></div>
            <div className="guide-flow">
              <GuideStep number="01" title="Validate" description="File type, size, URL and permissions are checked." />
              <GuideStep number="02" title="Parse & chunk" description="LlamaIndex extracts content and builds retrieval chunks." />
              <GuideStep number="03" title="Embed & index" description="Vectors and RBAC metadata are written to Qdrant." />
              <GuideStep number="04" title="Ready to retrieve" description="Authorized roles can search the new source." last />
            </div>
          </section>
          <div className="admin-tip"><AlertCircle size={15} /><p>Access grants are applied before retrieval, not after answer generation. A user's role cannot retrieve chunks outside its allow-list.</p></div>
        </aside>
      </div>
    </div>
  );
}

interface StatCardProps {
  icon: ReactNode;
  label: string;
  value: string;
  foot: string;
  tone: 'blue' | 'violet' | 'green' | 'orange';
}

function StatCard({ icon, label, value, foot, tone }: StatCardProps) {
  return <div className="stat-card"><span className={`stat-icon ${tone}`}>{icon}</span><div className="stat-value">{value}</div><div className="stat-label">{label}</div><div className="stat-foot">{foot}</div></div>;
}

function TelemetryRow({ label, value, state }: { label: string; value: string; state?: string }) {
  return <div className="telemetry-row"><span>{label}</span><strong>{value}{state && <small>{state}</small>}</strong></div>;
}

function GuideStep({ number, title, description, last = false }: { number: string; title: string; description: string; last?: boolean }) {
  return <div className={`guide-step ${last ? 'last' : ''}`}><span className="guide-number">{number}</span><div><strong>{title}</strong><small>{description}</small></div><ArrowUpRight size={14} /></div>;
}

function MessageIcon() {
  return <span className="message-icon-glyph">↗</span>;
}
