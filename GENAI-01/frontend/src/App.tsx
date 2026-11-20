import { useEffect, useState } from 'react';
import {
  Activity, ArrowUpRight, BookOpen, ChevronDown, CircleHelp, Command,
  Database, LayoutDashboard, LockKeyhole, LogOut, MessageSquare,
  Settings2, ShieldCheck, Sparkles,
} from 'lucide-react';
import { ApiError, checkHealth, createDemoSession, sessionStore } from './api/client';
import RoleSelector from './components/RoleSelector';
import AdminPage from './pages/AdminPage';
import ChatPage from './pages/ChatPage';
import type { Role, Session } from './types';

 type Page = 'chat' | 'admin';

export default function App() {
  const [session, setSession] = useState<Session | null>(() => sessionStore.get());
  const [page, setPage] = useState<Page>('chat');
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<'checking' | 'online' | 'offline'>('checking');
  const [signedOut, setSignedOut] = useState(false);
  const [vectorStore, setVectorStore] = useState('—');

  useEffect(() => {
    if (session || signedOut) return;
    setAuthBusy(true);
    void createDemoSession('nexvia-reviewer', 'guest')
      .then(setSession)
      .catch((cause: unknown) => setAuthError(cause instanceof Error ? cause.message : 'Unable to create a demo session.'))
      .finally(() => setAuthBusy(false));
  }, [session, signedOut]);

  useEffect(() => {
    let active = true;
    void checkHealth()
      .then((health) => {
        if (!active) return;
        setApiStatus('online');
        setVectorStore(health.vector_store.toUpperCase());
      })
      .catch(() => {
        if (active) setApiStatus('offline');
      });
    return () => { active = false; };
  }, [session?.accessToken]);

  async function switchRole(role: Role): Promise<void> {
    setAuthBusy(true);
    setAuthError(null);
    try {
      const username = session?.username ?? 'nexvia-reviewer';
      const nextSession = await createDemoSession(username, role);
      setSession(nextSession);
      if (role !== 'admin' && page === 'admin') setPage('chat');
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Role switch failed.';
      setAuthError(cause instanceof ApiError && cause.status === 404
        ? 'The demo role switcher is disabled by this deployment. Configure your identity provider to obtain a session.'
        : message);
    } finally {
      setAuthBusy(false);
    }
  }

  function signOut(): void {
    sessionStore.clear();
    setSignedOut(true);
    setSession(null);
    setPage('chat');
  }

  if (authBusy && !session) {
    return <div className="boot-screen"><div className="boot-mark"><Sparkles size={22} /></div><strong>Preparing secure workspace</strong><span>Connecting to Nexvia Knowledge Engine…</span></div>;
  }

  if (!session) {
    return (
      <div className="auth-error-screen">
        <div className="auth-error-card">
          <div className="brand-mark"><Sparkles size={22} /></div>
          <span className="eyebrow">NEXVIA KNOWLEDGE PLATFORM</span>
          <h1>Workspace authentication unavailable</h1>
          <p>{authError ?? 'The API did not issue a session token.'}</p>
          <div className="auth-error-actions">
            <button className="button button-primary" onClick={() => { setSignedOut(false); setSession(null); setAuthError(null); }}>Retry demo sign-in</button>
            <span>API: {import.meta.env.VITE_API_BASE_URL || 'same origin / Vite proxy'}</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark"><Sparkles size={21} strokeWidth={2.1} /></div>
          <div><strong>nexvia<span>.</span></strong><small>KNOWLEDGE PLATFORM</small></div>
        </div>

        <div className="workspace-switcher">
          <div className="workspace-avatar"><Command size={16} /></div>
          <div><strong>Enterprise workspace</strong><small>Evaluation environment</small></div>
          <ChevronDown size={14} />
        </div>

        <div className="sidebar-section-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          <button className={`nav-item ${page === 'chat' ? 'active' : ''}`} onClick={() => setPage('chat')}>
            <MessageSquare size={17} /><span>Knowledge assistant</span><span className="nav-shortcut">⌘ 1</span>
          </button>
          <button className={`nav-item ${page === 'admin' ? 'active' : ''}`} onClick={() => setPage('admin')}>
            <LayoutDashboard size={17} /><span>Admin console</span>{session.role === 'admin' ? <span className="admin-nav-tag">ADMIN</span> : <LockKeyhole className="nav-lock" size={13} />}
          </button>
        </nav>

        <div className="sidebar-section-label knowledge-label">KNOWLEDGE</div>
        <div className="sidebar-info-link"><BookOpen size={16} /><span>Source library</span><span className="soon-tag">INDEXED</span></div>
        <div className="sidebar-info-link"><Activity size={16} /><span>Evaluation traces</span><ArrowUpRight size={13} /></div>

        <div className="sidebar-bottom">
          <div className="sidebar-security"><span><ShieldCheck size={16} /></span><div><strong>Governed retrieval</strong><small>RBAC filters enabled</small></div><i /></div>
          <div className="sidebar-user">
            <div className="sidebar-user-avatar">{session.username.slice(0, 1).toUpperCase()}</div>
            <div className="sidebar-user-copy"><strong>{session.username}</strong><small>{session.role.toUpperCase()} ACCESS</small></div>
            <button className="sidebar-logout" onClick={signOut} aria-label="Sign out"><LogOut size={15} /></button>
          </div>
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="topbar-left"><span className="topbar-status-icon"><Database size={14} /></span><span>Knowledge systems</span><span className="topbar-slash">/</span><strong>{page === 'chat' ? 'Assistant' : 'Administration'}</strong></div>
          <div className="topbar-right">
            <div className={`api-health ${apiStatus}`}><i />{apiStatus === 'checking' ? 'Checking API' : apiStatus === 'online' ? `API ONLINE · ${vectorStore}` : 'API OFFLINE'}</div>
            <div className="topbar-divider" />
            <RoleSelector role={session.role} disabled={authBusy} onChange={(role) => void switchRole(role)} />
            <div className="topbar-user-avatar">{session.username.slice(0, 1).toUpperCase()}</div>
          </div>
        </header>

        {authError && <div className="global-alert" role="alert"><CircleHelp size={15} /> {authError}<button onClick={() => setAuthError(null)}>Dismiss</button></div>}
        <div className="main-content">
          {page === 'chat'
            ? <ChatPage username={session.username} activeRole={session.role} />
            : <AdminPage role={session.role} />}
        </div>
        <footer className="app-footer"><span>© 2026 Nexvia Technologies · Internal evaluation build</span><span><Settings2 size={13} /> Secure knowledge operations</span></footer>
      </main>
    </div>
  );
}
