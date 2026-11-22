import React from 'react';
import { AlertCircle, AlertTriangle, Info, Check, Sparkles } from 'lucide-react';

export default function IssueCard({
  issue,
  sourceText,
  onApplyFix,
  onManualCorrection,
  onOpenManualEditor,
  onCancelManualEditor,
  onManualReplacementChange,
  manualEditorOpen = false,
  manualReplacement = '',
  manualCorrectionError = '',
  isApprovingManualCorrection = false,
  selectedReplacementOptionId,
  onReplacementOptionChange,
  isApplyingFix = false,
  isAuditStale = false
}) {
  const severityConfig = {
    CRITICAL: {
      border: 'border-slate-200 hover:border-slate-300',
      badge: 'bg-rose-50 text-rose-700 border-rose-200',
      icon: AlertCircle,
      accent: 'border-l-rose-500'
    },
    WARNING: {
      border: 'border-slate-200 hover:border-slate-300',
      badge: 'bg-amber-50 text-amber-700 border-amber-200',
      icon: AlertTriangle,
      accent: 'border-l-amber-500'
    },
    INFO: {
      border: 'border-slate-200 hover:border-slate-300',
      badge: 'bg-blue-50 text-blue-700 border-blue-200',
      icon: Info,
      accent: 'border-l-blue-500'
    }
  };

  const config = severityConfig[issue.severity] || severityConfig.INFO;
  const Icon = config.icon;
  const suggestion = issue.replacement_text || issue.suggestion;
  const displayedMatchedText = issue.trigger_text || issue.matched_text;
  const replacementText = issue.replacement_text;
  const replacementOptions = Array.isArray(issue.replacement_options)
    ? issue.replacement_options.filter((option) => (
      option
      && typeof option.issue_id === 'string'
      && typeof option.replacement_text === 'string'
      && Boolean(option.replacement_text.trim())
      && option.replacement_text.trim().length <= 1000
    ))
    : [];
  const hasReplacementChoices = replacementOptions.length > 1;
  const selectedOptionId = hasReplacementChoices
    && replacementOptions.some((option) => option.issue_id === selectedReplacementOptionId)
    ? selectedReplacementOptionId
    : '';
  const location = issue.location;
  const hasCurrentMatch = typeof sourceText === 'string'
    && typeof issue.matched_text === 'string'
    && Number.isInteger(location?.start)
    && Number.isInteger(location?.end)
    && location.start >= 0
    && location.end > location.start
    && Array.from(sourceText).slice(location.start, location.end).join('') === issue.matched_text;
  const validSingleReplacement = issue.auto_fixable === true
    && typeof issue.matched_text === 'string'
    && typeof replacementText === 'string'
    && Boolean(replacementText.trim())
    && replacementText.trim().length <= 1000
    && !/^[\s]*(?:avoid\b|do not\b|don't\b|use\s*:|add\s+(?:a\s+)?disclaimer\b|required disclaimer\b|compliance explanation\b|policy rationale\b)/i.test(replacementText)
    && replacementText.trim().toLowerCase() !== issue.matched_text?.trim().toLowerCase();
  const canApplyFix = hasCurrentMatch && (
    hasReplacementChoices || validSingleReplacement
  ) && !isAuditStale && !(
    issue.manual_review_required === true
    && issue.replacement_choice_required !== true
  );
  const unavailableReason = isAuditStale
    ? 'The audit is stale. Run the audit again before applying this correction.'
    : !hasCurrentMatch
      ? 'The source text changed. Run the audit again before applying this correction.'
      : (
        issue.manual_review_required === true
        && issue.replacement_choice_required !== true
      )
        ? (issue.manual_review_reason || 'This replacement requires manual review.')
        : '';
  const requiresManualReview = issue.manual_review_required === true
    || issue.auto_fixable === false;
  const validManualReplacement = typeof manualReplacement === 'string'
    && Boolean(manualReplacement.trim())
    && manualReplacement.trim().length <= 1000
    && manualReplacement.trim().toLowerCase() !== issue.matched_text?.trim().toLowerCase()
    && !/^[\s]*(?:avoid\b|do not\b|don't\b|use\s*:|add\s+(?:a\s+)?disclaimer\b|required disclaimer\b|compliance explanation\b|policy rationale\b)/i.test(manualReplacement);

  return (
    <div className={`p-4 rounded-xl border border-l-3 bg-white ${config.accent} ${config.border} shadow-xs transition-all`}>
      {/* Badges & Rule Title */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold border ${config.badge}`}>
            <Icon className="w-3 h-3" />
            {issue.severity}
          </span>
          <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-slate-100 text-slate-700">
            {issue.category}
          </span>
        </div>
        <span className="text-[11px] font-semibold text-slate-600 truncate max-w-[200px]" title={issue.rule}>
          {issue.rule}
        </span>
      </div>

      {/* Matched text quote */}
      {displayedMatchedText && (
        <div className="my-2 p-2 rounded-lg bg-rose-50/70 border border-rose-100 text-xs font-mono text-rose-900 flex items-center gap-2">
          <span className="text-[10px] uppercase font-bold text-rose-500 select-none">Found:</span>
          <span className="font-semibold underline decoration-rose-300">
            "{displayedMatchedText}"
          </span>
        </div>
      )}

      {/* Policy Explanation */}
      <p className="text-xs text-slate-600 leading-relaxed mb-3">
        {issue.explanation}
      </p>

      {/* Suggested Fix Row */}
      {suggestion && (
        <div className="pt-2.5 border-t border-slate-100 flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-1.5 text-xs text-emerald-700 flex-1 min-w-[200px]">
            <Sparkles className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
            {hasReplacementChoices ? (
              <>
                <label htmlFor={`replacement-choice-${issue.id}`} className="text-slate-500 font-medium">
                  Choose replacement:
                </label>
                <select
                  id={`replacement-choice-${issue.id}`}
                  value={selectedOptionId}
                  onChange={(event) => onReplacementOptionChange?.(issue.id, event.target.value)}
                  disabled={!hasCurrentMatch || isApplyingFix}
                  className="min-w-0 max-w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700"
                >
                  <option value="" disabled>Choose a safe replacement...</option>
                  {replacementOptions.map((option) => (
                    <option key={option.issue_id} value={option.issue_id}>
                      {option.replacement_text}{option.rule ? ` — ${option.rule}` : ''}
                    </option>
                  ))}
                </select>
              </>
            ) : (
              <>
                <span className="text-slate-500 font-medium">Recommended:</span>
                <span className="font-semibold text-emerald-800">"{suggestion}"</span>
              </>
            )}
          </div>

          {canApplyFix && onApplyFix && (
            <button
              onClick={() => onApplyFix(
                issue,
                hasReplacementChoices ? selectedOptionId : issue.id
              )}
              disabled={isApplyingFix || (hasReplacementChoices && !selectedOptionId)}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
              title="Replace the exact matched text"
            >
              <Check className="w-3 h-3" />
              <span>Apply</span>
            </button>
          )}
        </div>
      )}

      {unavailableReason && (
        <p className="mt-2 text-[11px] text-amber-700">
          {unavailableReason}
        </p>
      )}

      {requiresManualReview && onManualCorrection && (
        <div className="mt-2">
          {!manualEditorOpen ? (
            <button
              type="button"
              onClick={() => onOpenManualEditor?.(issue)}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 transition-colors"
            >
              Review &amp; Edit
            </button>
          ) : (
            <section
              aria-label={`Manual correction editor for ${issue.rule}`}
              className="mt-3 rounded-lg border border-blue-200 bg-blue-50/40 p-3 space-y-2"
            >
              <h3 className="text-xs font-semibold text-slate-800">Review &amp; Edit</h3>
              <div>
                <p className="text-[11px] font-semibold text-slate-600">Original detected text</p>
                <p className="mt-1 rounded-md bg-white border border-slate-200 p-2 text-xs text-slate-800 whitespace-pre-wrap">
                  {issue.matched_text || 'Source text unavailable'}
                </p>
                {issue.trigger_text && issue.trigger_text !== issue.matched_text && (
                  <p className="mt-1 text-[10px] text-slate-500">
                    Detected term: {issue.trigger_text}
                  </p>
                )}
              </div>
              <div>
                <p className="text-[11px] font-semibold text-slate-600">Issue</p>
                <p className="text-xs text-slate-800">{issue.category}</p>
                {issue.explanation && (
                  <p className="mt-1 text-[11px] leading-relaxed text-slate-600">
                    {issue.explanation}
                  </p>
                )}
              </div>
              {suggestion && (
                <div>
                  <p className="text-[11px] font-semibold text-slate-600">Suggested replacement</p>
                  <p className="mt-1 text-xs text-slate-700 whitespace-pre-wrap">{suggestion}</p>
                </div>
              )}
              <label className="block text-[11px] font-semibold text-slate-600">
                Your correction
                <textarea
                  aria-label="Your correction"
                  value={manualReplacement}
                  onChange={(event) => onManualReplacementChange?.(
                    issue.id,
                    event.target.value,
                  )}
                  maxLength={1000}
                  rows={3}
                  className="mt-1 w-full rounded-md border border-slate-200 bg-white p-2 text-xs font-normal text-slate-800 focus:border-blue-500 focus:outline-none"
                />
              </label>
              {manualCorrectionError && (
                <p role="alert" className="text-[11px] text-rose-700">
                  {manualCorrectionError}
                </p>
              )}
              {(isAuditStale || !hasCurrentMatch) && (
                <p className="text-[11px] text-amber-700">
                  {isAuditStale
                    ? 'The audit is stale. Run it again before approving this correction.'
                    : 'The original source span changed. Run the audit again before approving this correction.'}
                </p>
              )}
              {!validManualReplacement && manualReplacement && (
                <p className="text-[11px] text-amber-700">
                  Enter different literal replacement text (1–1000 characters).
                </p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => onCancelManualEditor?.(issue.id)}
                  disabled={isApprovingManualCorrection}
                  className="px-2.5 py-1 rounded-md text-xs font-semibold bg-white text-slate-700 border border-slate-200 hover:bg-slate-50 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => onManualCorrection(issue, manualReplacement.trim())}
                  disabled={
                    isApprovingManualCorrection
                    || isAuditStale
                    || !hasCurrentMatch
                    || !validManualReplacement
                  }
                  className="px-2.5 py-1 rounded-md text-xs font-semibold bg-blue-600 text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isApprovingManualCorrection ? 'Approving...' : 'Approve Correction'}
                </button>
              </div>
            </section>
          )}
        </div>
      )}

      {/* Approved fact context */}
      {issue.approved_fact && (
        <div className="mt-2 text-[11px] text-slate-500 bg-slate-50 p-2 rounded-lg border border-slate-200">
          <span className="font-semibold text-slate-700">Approved Fact:</span> {issue.approved_fact}
        </div>
      )}
      {issue.required_disclaimer && (
        <div className="mt-2 text-[11px] text-slate-500 bg-slate-50 p-2 rounded-lg border border-slate-200">
          <span className="font-semibold text-slate-700">Required Disclaimer:</span> {issue.required_disclaimer}
        </div>
      )}
    </div>
  );
}
