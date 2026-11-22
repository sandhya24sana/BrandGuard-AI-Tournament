import React, { useState } from 'react';
import { Copy, Check, Sparkles } from 'lucide-react';

export default function DiffViewer({
  originalText,
  correctedText,
  onApplyAll,
  complianceApproved = false,
  heading = 'Brand Revision',
  originalLabel = 'Original Copy',
  correctedLabel = 'Brand-Aligned Version',
  showComplianceStatus = true,
}) {
  const [viewMode, setViewMode] = useState('split');
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (!correctedText) return;
    navigator.clipboard.writeText(correctedText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!correctedText) {
    return (
      <div className="p-8 text-center text-slate-400 text-xs">
        No corrected revision generated yet. Run analysis to view brand-aligned copy.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-white rounded-xl border border-slate-200 overflow-hidden shadow-xs">
      {/* Diff Toolbar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-slate-50/80 border-b border-slate-200 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-700 flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-blue-600" />
            {heading}
          </span>
          <div className="flex items-center bg-slate-200/80 rounded-lg p-0.5">
            <button
              onClick={() => setViewMode('split')}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                viewMode === 'split' ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Side-by-Side
            </button>
            <button
              onClick={() => setViewMode('inline')}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                viewMode === 'inline' ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Unified
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleCopy}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium bg-white hover:bg-slate-100 text-slate-700 border border-slate-200 transition-colors shadow-xs"
          >
            {copied ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3 text-slate-500" />}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
          {onApplyAll && (
            <button
              onClick={onApplyAll}
              className="inline-flex items-center gap-1 px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white shadow-xs transition-colors"
            >
              <Check className="w-3 h-3" />
              <span>Adopt All</span>
            </button>
          )}
        </div>
      </div>

      {/* Diff View Content */}
      <div className="p-4 flex-1 overflow-y-auto text-xs sm:text-sm leading-relaxed">
        {viewMode === 'split' ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 h-full">
            {/* Original Draft */}
            <div className="flex flex-col bg-slate-50 p-4 rounded-xl border border-slate-200">
              <div className="flex items-center justify-between text-xs font-semibold text-slate-600 mb-2 pb-2 border-b border-slate-200">
                <span>{originalLabel}</span>
                <span className="text-[10px] text-slate-400 uppercase font-medium">Unchecked</span>
              </div>
              <div className="text-slate-700 whitespace-pre-wrap leading-relaxed font-sans">
                {originalText}
              </div>
            </div>

            {/* Revised Copy */}
            <div className="flex flex-col bg-emerald-50/50 p-4 rounded-xl border border-emerald-200">
              <div className="flex items-center justify-between text-xs font-semibold text-emerald-800 mb-2 pb-2 border-b border-emerald-200">
                <span>{correctedLabel}</span>
                {showComplianceStatus && (
                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${
                    complianceApproved
                      ? 'bg-emerald-100 text-emerald-800'
                      : 'bg-amber-100 text-amber-800'
                  }`}>
                    {complianceApproved ? 'Compliance Approved' : 'Needs Review'}
                  </span>
                )}
              </div>
              <div className="text-slate-800 whitespace-pre-wrap leading-relaxed font-sans font-medium">
                {correctedText}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
              <span className="text-xs font-semibold text-slate-500 block mb-1">{originalLabel}:</span>
              <p className="text-slate-700 text-xs whitespace-pre-wrap">{originalText}</p>
            </div>
            <div className="p-3 bg-emerald-50/50 rounded-xl border border-emerald-200">
              <span className="text-xs font-semibold text-emerald-700 block mb-1">{correctedLabel}:</span>
              <p className="text-slate-800 text-xs font-medium whitespace-pre-wrap">{correctedText}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
