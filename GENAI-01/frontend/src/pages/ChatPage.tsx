import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowDown, Bot, Check, ChevronDown, CircleHelp, Copy, FileSearch,
  Gauge, LoaderCircle, MessageSquareText, Plus, RefreshCw, Send, ShieldCheck,
  Sparkles, ThumbsDown, ThumbsUp, Zap,
} from 'lucide-react';
import { ApiError, sendFeedback, streamChat } from '../api/client';
import CitationDialog from '../components/CitationDialog';
import type { ChatResponse, Citation, FeedbackRating, RetrievalMode } from '../types';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  response?: ChatResponse;
  feedback?: FeedbackRating;
  streaming?: boolean;
  error?: boolean;
}

interface ChatPageProps {
  username: string;
  activeRole: string;
}

const STARTER_QUESTIONS = [
  'What controls govern access to confidential financial information?',
  'Summarise the incident response process and its escalation path.',
  'What engineering checks are required before a production release?',
];

function formatCost(value: number): string {
  return value < 0.0001 ? '<$0.0001' : `$${value.toFixed(4)}`;
}

export default function ChatPage({ username, activeRole }: ChatPageProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState('');
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>('hybrid_rerank');
  const [topK, setTopK] = useState(6);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const activeAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  useEffect(() => () => activeAbort.current?.abort(), []);

  const isEmpty = messages.length === 0;
  const promptCounter = useMemo(() => `${question.length.toLocaleString()} / 10,000`, [question.length]);

  function updateAssistant(messageId: string, update: Partial<ChatMessage>): void {
    setMessages((current) => current.map((message) =>
      message.id === messageId ? { ...message, ...update } : message,
    ));
  }

  async function submitQuestion(event?: FormEvent<HTMLFormElement>, suggested?: string): Promise<void> {
    event?.preventDefault();
    const cleanQuestion = (suggested ?? question).trim();
    if (!cleanQuestion || sending) return;
    if (cleanQuestion.length > 10000) {
      setError('Questions must contain 10,000 characters or fewer.');
      return;
    }

    setError(null);
    setQuestion('');
    setSending(true);
    const userId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    const controller = new AbortController();
    activeAbort.current = controller;
    setMessages((current) => [
      ...current,
      { id: userId, role: 'user', content: cleanQuestion },
      { id: assistantId, role: 'assistant', content: '', streaming: true },
    ]);

    try {
      const response = await streamChat(cleanQuestion, retrievalMode, topK, {
        onToken: (token) => {
          setMessages((current) => current.map((message) =>
            message.id === assistantId
              ? { ...message, content: message.content + token }
              : message,
          ));
        },
        onComplete: (complete) => updateAssistant(assistantId, {
          content: complete.answer,
          response: complete,
          streaming: false,
        }),
      }, controller.signal);
      updateAssistant(assistantId, { content: response.answer, response, streaming: false });
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'An unexpected error occurred.';
      updateAssistant(assistantId, {
        content: message,
        streaming: false,
        error: true,
      });
      setError(cause instanceof ApiError && cause.status === 401
        ? 'Your session may have expired. Refresh the demo session from the role selector.'
        : message);
    } finally {
      activeAbort.current = null;
      setSending(false);
      inputRef.current?.focus();
    }
  }

  async function recordFeedback(messageId: string, requestId: string, rating: FeedbackRating): Promise<void> {
    try {
      await sendFeedback(requestId, rating);
      updateAssistant(messageId, { feedback: rating });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Feedback could not be saved.');
    }
  }

  async function copyAnswer(message: ChatMessage): Promise<void> {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessageId(message.id);
      window.setTimeout(() => setCopiedMessageId(null), 1600);
    } catch {
      setError('Clipboard access is unavailable in this browser context.');
    }
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion();
    }
  }

  function newConversation(): void {
    activeAbort.current?.abort();
    setMessages([]);
    setError(null);
    setQuestion('');
    setSending(false);
    inputRef.current?.focus();
  }

  return (
    <div className="chat-layout">
      <div className="chat-main-column">
        <div className="chat-topline">
          <div>
            <div className="breadcrumb">WORKSPACE <span>/</span> KNOWLEDGE ASSISTANT</div>
            <h1>Enterprise knowledge, <span>grounded in evidence.</span></h1>
            <p className="page-subtitle">Ask questions across governed company documents. Every answer is traceable to its source.</p>
          </div>
          <button className="button button-secondary new-chat-button" onClick={newConversation}>
            <Plus size={16} /> New chat
          </button>
        </div>

        <div className="security-strip">
          <span className="security-dot"><ShieldCheck size={15} /></span>
          <span>Role-scoped retrieval is active. Your answers only use documents your role is permitted to access.</span>
          <span className="security-label">{activeRole.toUpperCase()} ACCESS</span>
        </div>

        <div className={`conversation-panel ${isEmpty ? 'conversation-empty' : ''}`}>
          {isEmpty ? (
            <div className="empty-state">
              <div className="empty-orbit"><div className="empty-icon"><Sparkles size={27} /></div></div>
              <span className="eyebrow">NEXVIA KNOWLEDGE ENGINE</span>
              <h2>What would you like to know?</h2>
              <p>Search policies, financial filings, and technical documentation with source-backed answers.</p>
              <div className="starter-grid">
                {STARTER_QUESTIONS.map((starter, index) => (
                  <button className="starter-card" key={starter} onClick={() => void submitQuestion(undefined, starter)}>
                    <span className="starter-index">0{index + 1}</span>
                    <span>{starter}</span>
                    <ArrowUpRightIcon />
                  </button>
                ))}
              </div>
              <div className="empty-footnote"><FileSearch size={15} /> Answers are grounded in your authorised knowledge sources.</div>
            </div>
          ) : (
            <div className="message-list" aria-live="polite">
              {messages.map((message) => (
                <article className={`message-row ${message.role}`} key={message.id}>
                  <div className={`message-avatar ${message.role}`}>
                    {message.role === 'assistant' ? <Bot size={18} /> : <span>{username.slice(0, 1).toUpperCase()}</span>}
                  </div>
                  <div className="message-content-wrap">
                    <div className="message-meta">
                      <strong>{message.role === 'assistant' ? 'Nexvia Assistant' : username}</strong>
                      <span>{message.role === 'assistant' ? 'Knowledge engine' : 'You'}</span>
                    </div>
                    <div className={`message-bubble ${message.error ? 'message-error' : ''}`}>
                      {message.streaming && message.content.length === 0 ? (
                        <div className="thinking-state"><LoaderCircle className="spin" size={16} /> Searching authorised sources and preparing a grounded answer…</div>
                      ) : (
                        <p className="answer-text">{message.content}{message.streaming && <span className="stream-caret" />}</p>
                      )}
                      {message.streaming && message.content.length > 0 && <span className="stream-status"><span /> Generating response</span>}
                    </div>
                    {message.role === 'assistant' && message.response && (
                      <div className="answer-extras">
                        {message.response.citations.length > 0 && (
                          <div className="citation-section">
                            <div className="section-mini-label"><FileSearch size={13} /> SOURCES <span>{message.response.citations.length}</span></div>
                            <div className="citation-list">
                              {message.response.citations.map((citation) => (
                                <button
                                  className="citation-badge"
                                  key={citation.chunk_id}
                                  onClick={() => setSelectedCitation(citation)}
                                  title={`View citation evidence from ${citation.source}`}
                                >
                                  <FileSearch size={13} />
                                  <span>{citation.source}{citation.page == null ? '' : ` · p. ${citation.page}`}</span>
                                  <ArrowUpRightIcon />
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                        <div className="answer-toolbar">
                          <div className="answer-metrics">
                            <span><Gauge size={13} /> {message.response.latency_ms.toFixed(0)} ms</span>
                            <span><Zap size={13} /> {message.response.usage.total_tokens} tokens</span>
                            <span>{formatCost(message.response.usage.estimated_cost_usd)}</span>
                            {message.response.cache_hit && <span className="cache-chip"><RefreshCw size={11} /> Cache hit</span>}
                            <span className={message.response.abstained ? 'abstain-chip' : 'confidence-chip'}>
                              {message.response.abstained ? 'Abstained' : `${(message.response.confidence * 100).toFixed(0)}% confidence`}
                            </span>
                          </div>
                          <div className="answer-actions">
                            <button className="tool-button" onClick={() => void copyAnswer(message)} aria-label="Copy answer">
                              {copiedMessageId === message.id ? <Check size={14} /> : <Copy size={14} />}
                              <span>{copiedMessageId === message.id ? 'Copied' : 'Copy'}</span>
                            </button>
                            <span className="feedback-divider" />
                            {message.feedback ? (
                              <span className="feedback-thanks">Feedback recorded</span>
                            ) : (
                              <>
                                <button className="tool-button feedback-button" onClick={() => void recordFeedback(message.id, message.response!.request_id, 'up')} aria-label="Helpful answer"><ThumbsUp size={14} /></button>
                                <button className="tool-button feedback-button" onClick={() => void recordFeedback(message.id, message.response!.request_id, 'down')} aria-label="Unhelpful answer"><ThumbsDown size={14} /></button>
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                    {message.role === 'assistant' && message.error && (
                      <div className="answer-error-note"><CircleHelp size={13} /> Check the API connection and your current demo role.</div>
                    )}
                  </div>
                </article>
              ))}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        <div className="composer-wrap">
          {error && <div className="inline-alert" role="alert"><CircleHelp size={15} /> {error}<button onClick={() => setError(null)}>Dismiss</button></div>}
          <form className="composer" onSubmit={(event) => void submitQuestion(event)}>
            <textarea
              ref={inputRef}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder="Ask about policies, financials, or engineering documentation…"
              rows={2}
              maxLength={10000}
              disabled={sending}
              aria-label="Ask the enterprise knowledge assistant"
            />
            <div className="composer-bottom">
              <div className="composer-controls">
                <label className="compact-select-label">
                  <span>Retrieval</span>
                  <select value={retrievalMode} onChange={(event) => setRetrievalMode(event.target.value as RetrievalMode)} disabled={sending}>
                    <option value="dense">Dense only</option>
                    <option value="hybrid">Hybrid · RRF</option>
                    <option value="hybrid_rerank">Hybrid + rerank</option>
                  </select>
                  <ChevronDown size={12} />
                </label>
                <label className="compact-select-label topk-label">
                  <span>Sources</span>
                  <select value={topK} onChange={(event) => setTopK(Number(event.target.value))} disabled={sending}>
                    {[3, 6, 8, 10, 12].map((count) => <option key={count} value={count}>{count}</option>)}
                  </select>
                  <ChevronDown size={12} />
                </label>
                <span className="character-count">{promptCounter}</span>
              </div>
              <button className="send-button" type="submit" disabled={!question.trim() || sending} aria-label="Send question">
                {sending ? <LoaderCircle className="spin" size={17} /> : <Send size={16} />}
                <span>{sending ? 'Working' : 'Ask'}</span>
              </button>
            </div>
          </form>
          <div className="composer-disclaimer">AI-generated responses can contain errors. Verify important decisions against cited source material.</div>
        </div>
      </div>

      <aside className="chat-insights">
        <div className="insight-card user-card">
          <div className="insight-card-heading"><span className="eyebrow">SESSION CONTEXT</span><span className="live-indicator"><i /> LIVE</span></div>
          <div className="user-card-person">
            <div className="user-avatar">{username.slice(0, 1).toUpperCase()}</div>
            <div><strong>{username}</strong><span>Authenticated session</span></div>
          </div>
          <div className="role-access-row"><span>Access profile</span><strong>{activeRole.charAt(0).toUpperCase() + activeRole.slice(1)}</strong></div>
          <div className="role-access-row"><span>Retrieval policy</span><strong className="policy-active"><ShieldCheck size={13} /> Enforced</strong></div>
        </div>
        <div className="insight-card">
          <div className="insight-heading"><span className="insight-icon blue"><FileSearch size={16} /></span><h3>Retrieval pipeline</h3></div>
          <div className="pipeline-list">
            <div className="pipeline-step"><span className="pipeline-number">01</span><div><strong>Role-filtered search</strong><small>Qdrant payload RBAC before retrieval</small></div><Check size={14} /></div>
            <div className="pipeline-step"><span className="pipeline-number">02</span><div><strong>Hybrid candidate fusion</strong><small>Dense vectors + BM25 with RRF</small></div><Check size={14} /></div>
            <div className="pipeline-step"><span className="pipeline-number">03</span><div><strong>Precision reranking</strong><small>Cohere rerank when configured</small></div><Check size={14} /></div>
            <div className="pipeline-step"><span className="pipeline-number">04</span><div><strong>Grounded generation</strong><small>Citations required; abstain if unsupported</small></div><Check size={14} /></div>
          </div>
        </div>
        <div className="insight-card trust-card">
          <div className="insight-heading"><span className="insight-icon green"><ShieldCheck size={16} /></span><h3>Trust & governance</h3></div>
          <ul className="trust-list">
            <li><Check size={14} /> Document-level access controls</li>
            <li><Check size={14} /> Prompt-injection input screening</li>
            <li><Check size={14} /> Evidence-linked responses</li>
            <li><Check size={14} /> Safe abstention on weak evidence</li>
          </ul>
          <div className="trust-footer">Designed for auditable enterprise knowledge access.</div>
        </div>
        <div className="side-note"><MessageSquareText size={14} /><span>Use the retrieval selector to compare dense, hybrid, and reranked results during evaluation.</span></div>
      </aside>
      <CitationDialog citation={selectedCitation} onClose={() => setSelectedCitation(null)} />
    </div>
  );
}

function ArrowUpRightIcon() {
  return <ArrowDown className="starter-arrow" size={15} />;
}
