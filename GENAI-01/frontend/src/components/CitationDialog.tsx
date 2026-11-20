import { ExternalLink, FileText, X } from 'lucide-react';
import type { Citation } from '../types';

interface CitationDialogProps {
  citation: Citation | null;
  onClose: () => void;
}

export default function CitationDialog({ citation, onClose }: CitationDialogProps) {
  if (!citation) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="citation-dialog" role="dialog" aria-modal="true" aria-labelledby="citation-title">
        <div className="dialog-heading">
          <div className="dialog-title-wrap">
            <span className="icon-tile"><FileText size={18} /></span>
            <div>
              <p className="eyebrow">SOURCE EVIDENCE</p>
              <h2 id="citation-title">Citation details</h2>
            </div>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close citation details"><X size={19} /></button>
        </div>
        <div className="citation-source-name">{citation.source}</div>
        <div className="citation-meta-row">
          <span>{citation.page == null ? 'Web source / no page' : `Page ${citation.page}`}</span>
          <span>Relevance {(citation.score * 100).toFixed(1)}%</span>
        </div>
        <blockquote className="citation-quote">“{citation.quote}”</blockquote>
        <div className="citation-id">Evidence ID · {citation.chunk_id}</div>
        <button className="button button-secondary dialog-done" onClick={onClose}>
          Close <ExternalLink size={14} />
        </button>
      </section>
    </div>
  );
}
