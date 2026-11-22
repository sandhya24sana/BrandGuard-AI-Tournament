import React, { useState, useEffect, useRef } from 'react';
import { 
  Sparkles, Upload, FileText, CheckCircle2, AlertTriangle, ShieldCheck, 
  RotateCcw, Check, Layers, Clock, AlertCircle, XCircle
} from 'lucide-react';
import { api } from '../api';
import RiskScoreBadge from '../components/RiskScoreBadge';
import IssueCard from '../components/IssueCard';
import DiffViewer from '../components/DiffViewer';
import ReviewActionModal from '../components/ReviewActionModal';

export default function ContentAnalyzer({
  brandId,
  brandName,
  contentToEdit,
  onCancelEdit
}) {
  // Metadata
  const [title, setTitle] = useState('');
  const [contentType, setContentType] = useState('Ad Copy');
  const [channel, setChannel] = useState('Digital Ads');
  const [text, setText] = useState('');
  
  // State
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isVisualAnalyzing, setIsVisualAnalyzing] = useState(false);
  const [isRewriting, setIsRewriting] = useState(false);
  const [isSavingRewrite, setIsSavingRewrite] = useState(false);
  const [rewritePreview, setRewritePreview] = useState(null);
  const [rewriteError, setRewriteError] = useState('');
  const [contentId, setContentId] = useState(null);
  const [savedText, setSavedText] = useState('');
  const [currentVersion, setCurrentVersion] = useState(1);
  const [status, setStatus] = useState('draft');
  
  // Analysis Output
  const [analysisResult, setAnalysisResult] = useState(null);
  const [auditedVersion, setAuditedVersion] = useState(null);
  const [isAuditStale, setIsAuditStale] = useState(false);
  const [visualResult, setVisualResult] = useState(null);
  const [visualFileName, setVisualFileName] = useState('');
  const [visualError, setVisualError] = useState('');
  const [revisionOriginalText, setRevisionOriginalText] = useState(null);
  const [revisionText, setRevisionText] = useState(null);
  const [revisionComplianceApproved, setRevisionComplianceApproved] = useState(false);
  const [selectedReplacementOptions, setSelectedReplacementOptions] = useState({});
  const [activeRightTab, setActiveRightTab] = useState('issues'); // 'issues', 'diff', 'readability'
  const [selectedCategory, setSelectedCategory] = useState('ALL');

  // Review modal
  const [isReviewModalOpen, setIsReviewModalOpen] = useState(false);
  const [defaultReviewDecision, setDefaultReviewDecision] = useState('approved');

  // Notification Toast
  const [toastMessage, setToastMessage] = useState(null);
  const fileInputRef = useRef(null);
  const isApplyingFixRef = useRef(false);
  const rewriteRequestRef = useRef(0);
  const [isApplyingFix, setIsApplyingFix] = useState(false);

  const showToast = (msg, type = 'success') => {
    setToastMessage({ msg, type });
    setTimeout(() => setToastMessage(null), 3500);
  };

  const clearRevision = () => {
    setRevisionOriginalText(null);
    setRevisionText(null);
    setRevisionComplianceApproved(false);
  };

  const clearRewritePreview = () => {
    rewriteRequestRef.current += 1;
    setRewritePreview(null);
    setRewriteError('');
    setIsRewriting(false);
  };

  useEffect(() => {
    rewriteRequestRef.current += 1;
    setRewritePreview(null);
    setRewriteError('');
    setIsRewriting(false);
    const originalText = contentToEdit?.original_text || '';
    setTitle(contentToEdit?.title || '');
    setContentType(contentToEdit?.content_type || 'Ad Copy');
    setChannel(contentToEdit?.channel || 'Digital Ads');
    setText(originalText);
    setAnalysisResult(null);
    setAuditedVersion(null);
    setIsAuditStale(false);
    setVisualResult(null);
    setVisualFileName('');
    setVisualError('');
    setRevisionOriginalText(null);
    setRevisionText(null);
    setRevisionComplianceApproved(false);
    setSelectedReplacementOptions({});
    setContentId(contentToEdit?.id || null);
    setSavedText(originalText);
    setCurrentVersion(contentToEdit?.current_version || 1);
    setStatus(contentToEdit?.status || 'draft');
    setIsReviewModalOpen(false);
  }, [brandId, contentToEdit]);

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const isImageFile = file.type.startsWith('image/')
      || /\.(png|jpe?g|webp)$/i.test(file.name);
    setIsUploading(true);
    try {
      if (isImageFile) {
        setVisualError('');
        setVisualResult(null);
        setVisualFileName(file.name);
        if (!brandId) {
          throw new Error('Select a brand before requesting an image review.');
        }

        setIsVisualAnalyzing(true);
        const visualResponse = await api.analyzeVisualFile(file, brandId);
        if (!visualResponse.success || !visualResponse.visual_validation) {
          throw new Error(
            visualResponse.error || 'Could not complete the visual review.'
          );
        }
        setVisualResult(visualResponse.visual_validation);
        setVisualFileName(visualResponse.filename || file.name);
        showToast('Image review completed. Verify all findings manually.');
        return;
      }

      setVisualResult(null);
      setVisualFileName('');
      setVisualError('');
      const res = await api.uploadFile(file);
      if (res.success) {
        clearRewritePreview();
        setText(res.text);
        if (!contentToEdit) {
          setTitle(file.name.replace(/\.[^/.]+$/, ''));
          setContentId(null);
          setSavedText('');
          setCurrentVersion(1);
          setStatus('draft');
        }
        setAnalysisResult(null);
        clearRevision();
        setSelectedReplacementOptions({});
        showToast(`Document parsed (${res.word_count} words extracted).`);
      } else {
        showToast(res.error || 'Upload failed', 'error');
      }
    } catch (err) {
      if (isImageFile) {
        const message = err.message || 'Could not complete the visual review.';
        setVisualError(message);
        showToast('Visual review failed: ' + message, 'error');
      } else {
        showToast('Error uploading file: ' + err.message, 'error');
      }
    } finally {
      setIsUploading(false);
      setIsVisualAnalyzing(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleRunAnalysis = async () => {
    if (!brandId) {
      showToast('Create and select a brand before analyzing content.', 'error');
      return;
    }
    if (!text.trim()) {
      showToast('Please enter or upload content to analyze.', 'error');
      return;
    }

    setIsAnalyzing(true);
    try {
      if (contentToEdit && contentId) {
        const latestContent = await api.getContentDetail(contentId, brandId);
        if (!latestContent.success) {
          throw new Error(latestContent.error || 'Could not verify the current content version.');
        }
        if (latestContent.content?.brand_id !== brandId) {
          throw new Error('This content no longer belongs to the selected brand. Return to Review History and reopen it.');
        }
        if (latestContent.content?.current_version !== currentVersion) {
          throw new Error('Content changed after editing began. Return to Review History and edit the latest version.');
        }
      }

      const res = await api.analyzeContent({
        content_id: contentId,
        current_version: contentId ? currentVersion : undefined,
        brand_id: brandId,
        title: title.trim() || 'Untitled Content',
        content_type: contentType,
        channel,
        text
      });

      if (res.success && res.result) {
        if (res.content_id) {
          setContentId(res.content_id);
        }
        setSavedText(text);
        setAnalysisResult(res.result);
        setRevisionOriginalText(text);
        setRevisionText(res.result.corrected_text || text);
        setRevisionComplianceApproved(
          res.result.revision_compliance?.approved === true
        );
        setSelectedReplacementOptions({});
        setCurrentVersion(res.version || 1);
        setAuditedVersion(res.version || 1);
        setIsAuditStale(false);
        setStatus(res.status || 'pending_review');
        showToast(`Audit completed! Compliance score: ${res.result.score}/100.`);
      } else {
        showToast(res.error || 'Analysis failed', 'error');
      }
    } catch (err) {
      showToast('Analysis error: ' + err.message, 'error');
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleGenerateRewrite = async () => {
    if (!brandId) {
      showToast('Create and select a brand before requesting an AI rewrite.', 'error');
      return;
    }
    if (!text.trim()) {
      showToast('Please enter or upload content before requesting an AI rewrite.', 'error');
      return;
    }

    const requestId = ++rewriteRequestRef.current;
    const source = { brandId, text, contentType, channel };
    setRewritePreview(null);
    setRewriteError('');
    setIsRewriting(true);
    try {
      const res = await api.rewriteContent({
        brand_id: brandId,
        text,
        content_type: contentType,
        channel,
      });
      if (requestId !== rewriteRequestRef.current) return;
      if (!res.success) throw new Error(res.error || 'AI rewrite failed.');
      if (typeof res.rewritten_text !== 'string' || !res.rewritten_text.trim()) {
        throw new Error('The AI rewrite response did not contain valid rewritten text.');
      }
      setRewritePreview({ rewrittenText: res.rewritten_text, source });
    } catch (error) {
      if (requestId === rewriteRequestRef.current) {
        setRewriteError(error.message || 'AI rewrite failed. Please try again.');
      }
    } finally {
      if (requestId === rewriteRequestRef.current) setIsRewriting(false);
    }
  };

  const handleSaveRewrite = async () => {
    if (!rewritePreview || isSavingRewrite) return;
    if (!window.confirm('Save this AI rewrite as a new content version? The current version will remain in history.')) {
      return;
    }

    const { rewrittenText, source } = rewritePreview;
    if (
      source.brandId !== brandId ||
      source.text !== text ||
      source.contentType !== contentType ||
      source.channel !== channel
    ) {
      clearRewritePreview();
      setRewriteError('The source content or settings changed. Generate a new AI rewrite preview before saving.');
      return;
    }

    setIsSavingRewrite(true);
    setRewriteError('');
    try {
      if (contentId) {
        const latestContent = await api.getContentDetail(contentId, brandId);
        if (!latestContent.success) {
          throw new Error(latestContent.error || 'Could not verify the current content version.');
        }
        if (latestContent.content?.brand_id !== brandId) {
          throw new Error('This content no longer belongs to the selected brand. Return to Review History and reopen it.');
        }
        if (latestContent.content?.current_version !== currentVersion) {
          throw new Error('Content changed after this rewrite preview was created. Reload the latest version before saving.');
        }
      }

      const res = await api.analyzeContent({
        content_id: contentId || undefined,
        current_version: contentId ? currentVersion : undefined,
        brand_id: brandId,
        title: title.trim() || 'Untitled Content',
        content_type: contentType,
        channel,
        text: rewrittenText,
      });
      if (!res.success || !res.result) {
        throw new Error(res.error || 'Could not save the AI rewrite as a new version.');
      }

      setText(rewrittenText);
      setSavedText(rewrittenText);
      setContentId(res.content_id);
      setAnalysisResult(res.result);
      setRevisionOriginalText(rewrittenText);
      setRevisionText(res.result.corrected_text || rewrittenText);
      setRevisionComplianceApproved(
        res.result.revision_compliance?.approved === true
      );
      setSelectedReplacementOptions({});
      setCurrentVersion(res.version || 1);
      setAuditedVersion(res.version || 1);
      setIsAuditStale(false);
      setStatus(res.status || 'pending_review');
      clearRewritePreview();
      showToast(`Saved AI rewrite as version ${res.version || 1}.`);
    } catch (error) {
      setRewriteError(error.message || 'Could not save the AI rewrite.');
    } finally {
      setIsSavingRewrite(false);
    }
  };

  const handleReplacementOptionChange = (issueId, optionId) => {
    setSelectedReplacementOptions((current) => ({
      ...current,
      [issueId]: optionId,
    }));
  };

  const verifyCurrentAuditVersion = async () => {
    if (!contentId) {
      throw new Error('Run an audit before applying a correction.');
    }
    if (auditedVersion === null || auditedVersion !== currentVersion) {
      setIsAuditStale(true);
      throw new Error('The audited content version is stale. Run the audit again.');
    }

    const latestContent = await api.getContentDetail(contentId, brandId);
    if (!latestContent.success) {
      throw new Error(latestContent.error || 'Could not verify the current content version.');
    }
    if (latestContent.content?.brand_id !== brandId) {
      throw new Error('This content no longer belongs to the selected brand.');
    }
    if (latestContent.content?.current_version !== auditedVersion) {
      setIsAuditStale(true);
      throw new Error('The audited content version is stale. Run the audit again.');
    }
  };

  const handleApplySingleFix = async (issue, selectedIssueId = issue.id) => {
    if (isApplyingFixRef.current) return;
    if (!contentId) {
      showToast('Run an audit before applying a correction.', 'error');
      return;
    }
    const replacementOptions = Array.isArray(issue.replacement_options)
      ? issue.replacement_options
      : [];
    const choiceRequired = issue.replacement_choice_required === true
      || replacementOptions.length > 1;
    if (
      (choiceRequired && !replacementOptions.some(
        (option) => option.issue_id === selectedIssueId
      ))
      || (!choiceRequired && selectedIssueId !== issue.id)
    ) {
      showToast('Choose a validated replacement before applying it.', 'error');
      return;
    }

    isApplyingFixRef.current = true;
    setIsApplyingFix(true);
    try {
      await verifyCurrentAuditVersion();
      const res = await api.applyCorrections(contentId, {
        brand_id: brandId,
        issue_ids: [selectedIssueId],
        content_version: currentVersion,
      });
      if (!res.success) {
        if (
          res.status === 409
          && res.current_version !== undefined
          && res.current_version !== auditedVersion
        ) {
          setIsAuditStale(true);
        }
        throw new Error(res.error || 'Correction could not be saved.');
      }
      clearRewritePreview();
      setText(res.applied_text);
      setSavedText(res.applied_text);
      setRevisionText(res.applied_text);
      setRevisionComplianceApproved(
        res.correction_review?.compliant === true
      );
      setCurrentVersion(res.new_version);
      setAuditedVersion(res.new_version);
      setIsAuditStale(false);
      setAnalysisResult(res.result);
      setSelectedReplacementOptions({});
      setStatus('pending_review');
      showToast(`Applied correction and saved version ${res.new_version}. Manual review is still required.`);
    } catch (err) {
      showToast('Error applying correction: ' + err.message, 'error');
    } finally {
      isApplyingFixRef.current = false;
      setIsApplyingFix(false);
    }
  };

  const handleApplyAllCorrections = async () => {
    if (isApplyingFixRef.current) return;
    if (!contentId) {
      showToast('Run an audit before applying corrections.', 'error');
      return;
    }

    isApplyingFixRef.current = true;
    setIsApplyingFix(true);
    try {
      await verifyCurrentAuditVersion();
      const res = await api.applyCorrections(contentId, {
        brand_id: brandId,
        apply_all: true,
        content_version: currentVersion,
      });
      if (!res.success) {
        if (
          res.status === 409
          && res.current_version !== undefined
          && res.current_version !== auditedVersion
        ) {
          setIsAuditStale(true);
        }
        throw new Error(res.error || 'Corrections could not be saved.');
      }
      clearRewritePreview();
      setText(res.applied_text);
      setSavedText(res.applied_text);
      setRevisionText(res.applied_text);
      setRevisionComplianceApproved(
        res.correction_review?.compliant === true
      );
      setAnalysisResult(res.result);
      setCurrentVersion(res.new_version);
      setAuditedVersion(res.new_version);
      setIsAuditStale(false);
      setSelectedReplacementOptions({});
      setStatus('pending_review');
      showToast(`Applied all corrections (Updated to v${res.new_version}, Score: ${res.result.score}/100). Manual review is still required.`);
    } catch (err) {
      showToast('Error applying corrections: ' + err.message, 'error');
    } finally {
      isApplyingFixRef.current = false;
      setIsApplyingFix(false);
    }
  };

  const handleReviewDecision = async (reviewData) => {
    if (!brandId) throw new Error('Create and select a brand before submitting a review.');
    if (!text.trim()) throw new Error('Enter content before submitting it for review.');
    let targetId = contentId;
    let targetVersion = currentVersion;
    if (!targetId || savedText !== text) {
      const auditRes = await api.analyzeContent({
        content_id: targetId,
        current_version: targetId ? targetVersion : undefined,
        brand_id: brandId,
        title,
        content_type: contentType,
        channel,
        text
      });
      if (!auditRes.success) throw new Error(auditRes.error || 'Failed to save content before recording review.');
      targetId = auditRes.content_id;
      targetVersion = auditRes.version;
      setContentId(targetId);
      setSavedText(text);
      setAnalysisResult(auditRes.result);
      setRevisionOriginalText(text);
      setRevisionText(auditRes.result.corrected_text || text);
      setRevisionComplianceApproved(
        auditRes.result.revision_compliance?.approved === true
      );
      setCurrentVersion(targetVersion);
      setAuditedVersion(targetVersion);
      setIsAuditStale(false);
      setStatus('pending_review');
    }

    const res = await api.submitReview({
      ...reviewData,
      brand_id: brandId,
      content_id: targetId,
      content_version: targetVersion,
    });
    if (!res.success) throw new Error(res.error || 'Review recording failed.');
    setStatus(res.new_status);
    showToast(res.message);
  };

  const openReviewModalWithDecision = (decision) => {
    setDefaultReviewDecision(decision);
    setIsReviewModalOpen(true);
  };

  // Filter issues
  const filteredIssues = (analysisResult?.issues || []).filter(issue => {
    return selectedCategory === 'ALL' || issue.category === selectedCategory;
  });
  const isAuditVersionStale = Boolean(
    analysisResult
    && (isAuditStale || auditedVersion !== currentVersion)
  );

  const categories = ['ALL', 'Terminology', 'Fact Accuracy', 'Voice & Tone', 'Compliance & Claims', 'Accessibility & Clarity'];

  const getStatusBadge = () => {
    switch (status) {
      case 'approved':
        return <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 inline-flex items-center gap-1"><CheckCircle2 className="w-3 h-3" /> Approved</span>;
      case 'rejected':
        return <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-50 text-rose-700 border border-rose-200 inline-flex items-center gap-1"><XCircle className="w-3 h-3" /> Rejected</span>;
      case 'changes_requested':
        return <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200 inline-flex items-center gap-1"><RotateCcw className="w-3 h-3" /> Changes Requested</span>;
      case 'pending_review':
        return <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200 inline-flex items-center gap-1"><Clock className="w-3 h-3" /> Pending Review</span>;
      default:
        return <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600">Draft v{currentVersion}</span>;
    }
  };

  return (
    <div className="space-y-4">
      {/* Toast Alert */}
      {toastMessage && (
        <div className={`fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl border shadow-lg flex items-center gap-2 text-xs font-semibold animate-fadeIn ${
          toastMessage.type === 'error'
            ? 'bg-rose-50 border-rose-200 text-rose-800'
            : toastMessage.type === 'info'
            ? 'bg-blue-50 border-blue-200 text-blue-800'
            : 'bg-emerald-50 border-emerald-200 text-emerald-800'
        }`}>
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{toastMessage.msg}</span>
        </div>
      )}

      {/* Content Meta Controls Card */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex-1">
          <input
            type="text"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              setSavedText(null);
              setAnalysisResult(null);
              clearRevision();
              setStatus('draft');
            }}
            className="w-full text-sm sm:text-base font-bold text-slate-900 focus:outline-none focus:ring-1 focus:ring-blue-600 rounded px-1 -ml-1 placeholder-slate-400"
            placeholder="Campaign / Document Title..."
          />
          <div className="flex items-center gap-2 mt-1 text-xs text-slate-500">
            <span>Brand: <strong className="text-slate-800 font-semibold">{brandName}</strong></span>
            <span>•</span>
            {getStatusBadge()}
          </div>
        </div>

        {/* Content Type & Channel Controls */}
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={contentType}
            onChange={(e) => {
              clearRewritePreview();
              setContentType(e.target.value);
              setSavedText(null);
              setAnalysisResult(null);
              clearRevision();
              setStatus('draft');
            }}
            aria-label="Content Format"
            className="bg-slate-50 border border-slate-200 text-slate-700 text-xs font-medium rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-blue-600 cursor-pointer"
          >
            <option value="Ad Copy">Ad Copy (Social / Search)</option>
            <option value="Email">Email Newsletter</option>
            <option value="Social Post">Social Media Post</option>
            <option value="Press Release">Press Release</option>
            <option value="Document">Documentation</option>
          </select>

          <select
            value={channel}
            onChange={(e) => {
              clearRewritePreview();
              setChannel(e.target.value);
              setSavedText(null);
              setAnalysisResult(null);
              clearRevision();
              setStatus('draft');
            }}
            aria-label="Distribution Channel"
            className="bg-slate-50 border border-slate-200 text-slate-700 text-xs font-medium rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-blue-600 cursor-pointer"
          >
            <option value="Twitter / X & LinkedIn Ads">Twitter / X & LinkedIn</option>
            <option value="Email Newsletter">Email Newsletter</option>
            <option value="Website & PR Wire">Website & PR Wire</option>
            <option value="Print & Collateral">Print & Collateral</option>
            <option value="Customer Portal">Customer Portal</option>
          </select>

          {/* Hidden File Upload Input */}
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileUpload}
            accept=".txt,.md,.markdown,.docx,.pdf,.json,.png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
            className="hidden"
          />

          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading || isSavingRewrite}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-50 hover:bg-slate-100 text-slate-700 border border-slate-200 transition-colors"
            title="Upload a text document or a PNG, JPEG, or WebP image for visual review"
          >
            <Upload className="w-3.5 h-3.5 text-slate-500" />
            <span>
              {isVisualAnalyzing ? 'Reviewing image...' : isUploading ? 'Parsing...' : 'Upload File'}
            </span>
          </button>
        </div>
      </div>

      {/* Two-Column Workspace */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* LEFT COLUMN: Content Editor */}
        <div className="lg:col-span-7 bg-white rounded-xl border border-slate-200 p-5 shadow-xs flex flex-col">
          {/* Editor Header */}
          <div className="flex items-center justify-between pb-3 border-b border-slate-100 text-xs text-slate-500">
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4 text-blue-600" />
              <span className="font-semibold text-slate-900">Content Input Editor</span>
              {currentVersion > 1 && (
                <span className="text-[11px] text-slate-400 font-mono">
                  (v{currentVersion})
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 font-mono text-[11px]">
              <span>{text.split(/\s+/).filter(Boolean).length} words</span>
              <span>•</span>
              <span>{text.length} chars</span>
            </div>
          </div>

          {/* Text Area */}
          <div className="mt-3">
            <textarea
              value={text}
              onChange={(e) => {
                clearRewritePreview();
                setText(e.target.value);
                setAnalysisResult(null);
                clearRevision();
                setSelectedReplacementOptions({});
                setStatus('draft');
              }}
              placeholder="Paste or type content to audit..."
              className="w-full h-80 sm:h-[400px] bg-slate-50/50 border border-slate-200 rounded-lg p-4 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-blue-600 focus:bg-white leading-relaxed resize-none transition-colors"
            />
          </div>

          {/* Action Footer */}
          <div className="mt-4 pt-3.5 border-t border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <button
                onClick={handleRunAnalysis}
                disabled={isAnalyzing || isSavingRewrite}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 shadow-xs transition-colors disabled:opacity-50"
              >
                <Sparkles className={`w-3.5 h-3.5 ${isAnalyzing ? 'animate-spin' : ''}`} />
                <span>{isAnalyzing ? 'Auditing Policy...' : 'Audit Content'}</span>
              </button>

              <button
                onClick={handleGenerateRewrite}
                disabled={isRewriting || isSavingRewrite || !brandId || !text.trim()}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold text-slate-700 bg-white hover:bg-slate-50 border border-slate-200 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                title="Generate a Gemini rewrite preview without saving or changing the original"
              >
                <Sparkles className={`w-3.5 h-3.5 text-blue-600 ${isRewriting ? 'animate-spin' : ''}`} />
                <span>{isRewriting ? 'Generating AI rewrite...' : 'AI Rewrite'}</span>
              </button>

              {contentToEdit && onCancelEdit && (
                <button
                  onClick={onCancelEdit}
                  disabled={isAnalyzing}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  title="Discard unsaved edits and return to Review History"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  <span>Back to Review History</span>
                </button>
              )}

              {analysisResult?.corrected_text && analysisResult.corrected_text !== text && (
                <button
                  onClick={handleApplyAllCorrections}
                  disabled={isApplyingFix || isAuditVersionStale}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-emerald-50 hover:bg-emerald-100 text-emerald-800 border border-emerald-200 transition-colors"
                  title="Replace with brand-compliant text"
                >
                  <Check className="w-3.5 h-3.5" />
                  <span>Adopt All Corrections</span>
                </button>
              )}
            </div>

            {/* Clear Approve and Reject Action Buttons */}
            <div className="flex items-center gap-2 border-t sm:border-t-0 pt-2 sm:pt-0 border-slate-100">
              <button
                onClick={() => openReviewModalWithDecision('approved')}
                className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white shadow-xs transition-colors"
                title="Sign-off & Approve Content"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>Approve</span>
              </button>

              <button
                onClick={() => openReviewModalWithDecision('rejected')}
                className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-xs font-semibold bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 transition-colors"
                title="Reject with audit feedback"
              >
                <XCircle className="w-3.5 h-3.5" />
                <span>Reject</span>
              </button>
            </div>
          </div>

          {rewriteError && (
            <p role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
              {rewriteError}
            </p>
          )}

          {rewritePreview && (
            <section aria-label="AI Rewrite Preview" className="mt-4 rounded-xl border border-blue-200 bg-blue-50/50 p-4">
              <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                <div>
                  <h3 className="text-xs font-bold text-slate-900">AI Rewrite Preview</h3>
                  <p className="mt-1 text-[11px] text-slate-600">
                    Gemini-generated preview. Your original content remains unchanged until you save.
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={handleSaveRewrite}
                    disabled={isSavingRewrite}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Check className="w-3.5 h-3.5" />
                    <span>{isSavingRewrite ? 'Saving version...' : 'Save as New Version'}</span>
                  </button>
                  <button
                    onClick={clearRewritePreview}
                    disabled={isSavingRewrite}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Discard
                  </button>
                </div>
              </div>
              <div className="mt-3 rounded-lg border border-blue-100 bg-white p-3 text-sm text-slate-800 leading-relaxed whitespace-pre-wrap">
                {rewritePreview.rewrittenText}
              </div>
            </section>
          )}
        </div>

        {/* RIGHT COLUMN: Analysis & Governance Results */}
        <div className="lg:col-span-5 bg-white rounded-xl border border-slate-200 p-5 shadow-xs flex flex-col">
          {visualError && (
            <p role="alert" className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
              {visualError}
            </p>
          )}

          {visualResult && (
            <section aria-label="Visual brand review" className="mb-5 rounded-xl border border-violet-200 bg-violet-50/40 p-4">
              <div className="border-b border-violet-100 pb-3">
                <h2 className="text-xs font-bold uppercase tracking-wider text-slate-800">
                  Visual Brand Review
                </h2>
                <p className="mt-1 text-[11px] text-slate-600">
                  {visualFileName} — preliminary AI review; manually verify every result.
                </p>
                {visualResult.message && (
                  <p className="mt-2 text-[11px] text-slate-600">
                    {visualResult.message}
                  </p>
                )}
              </div>

              <div className="mt-3 space-y-2">
                {(visualResult.checks || []).map((check) => (
                  <div key={check.id} className="rounded-lg border border-slate-200 bg-white p-3">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-xs font-semibold text-slate-800">
                        {check.rule}
                      </h3>
                      <span className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-semibold ${
                        check.status === 'concern'
                          ? 'bg-amber-100 text-amber-800'
                          : check.status === 'no_obvious_concern'
                            ? 'bg-emerald-100 text-emerald-800'
                            : 'bg-slate-100 text-slate-700'
                      }`}>
                        {check.status === 'concern'
                          ? 'Concern'
                          : check.status === 'no_obvious_concern'
                            ? 'No obvious concern'
                            : 'Uncertain'}
                      </span>
                    </div>
                    <p className="mt-1.5 text-xs leading-relaxed text-slate-600">
                      {check.evidence}
                    </p>
                    <p className="mt-1 text-[10px] text-slate-500">
                      Confidence: {check.confidence || 'unknown'} · Manual review required
                    </p>
                  </div>
                ))}
              </div>

              {Array.isArray(visualResult.not_evaluated)
                && visualResult.not_evaluated.length > 0 && (
                  <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
                    <h3 className="text-[11px] font-bold text-amber-900">
                      Not evaluated
                    </h3>
                    <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-amber-900">
                      {visualResult.not_evaluated.map((item, index) => (
                        <li key={`${item.rule}-${index}`}>
                          <span className="font-semibold">{item.rule}:</span> {item.reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
            </section>
          )}

          {analysisResult ? (
            <div>
              {/* Compliance Score Header */}
              <div className="pb-4 border-b border-slate-100">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs font-bold uppercase tracking-wider text-slate-500">
                    Compliance Health
                  </span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-600">
                    {analysisResult.engine_used === 'AI_GEMINI' ? 'Gemini AI' : 'Rule-Based Fallback'}
                  </span>
                </div>

                <RiskScoreBadge
                  score={analysisResult.score}
                  riskLevel={analysisResult.risk_level}
                  subScores={analysisResult.sub_scores}
                />

                {/* Executive Assessment */}
                {analysisResult.summary && (
                  <div className="mt-3.5 p-3 rounded-lg bg-slate-50 border border-slate-100 text-xs text-slate-600 leading-relaxed">
                    <span className="font-semibold text-slate-900 block mb-0.5">Summary Assessment:</span>
                    {analysisResult.summary}
                  </div>
                )}
                {analysisResult.visual_validation && (
                  <p className="mt-2 text-[11px] text-slate-500">
                    {analysisResult.visual_validation.message}
                  </p>
                )}
              </div>

              {/* Panel Tabs */}
              <div className="flex items-center gap-1 mt-4 p-1 bg-slate-100 rounded-lg text-xs font-medium">
                <button
                  onClick={() => setActiveRightTab('issues')}
                  className={`flex-1 py-1.5 px-3 rounded-md transition-colors ${
                    activeRightTab === 'issues'
                      ? 'bg-white text-slate-900 font-semibold shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  Issues ({analysisResult.issues?.length || 0})
                </button>

                <button
                  onClick={() => setActiveRightTab('diff')}
                  className={`flex-1 py-1.5 px-3 rounded-md transition-colors ${
                    activeRightTab === 'diff'
                      ? 'bg-white text-slate-900 font-semibold shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  Diff & Changes
                </button>

                <button
                  onClick={() => setActiveRightTab('readability')}
                  className={`flex-1 py-1.5 px-3 rounded-md transition-colors ${
                    activeRightTab === 'readability'
                      ? 'bg-white text-slate-900 font-semibold shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  Readability
                </button>
              </div>

              {/* TAB 1: Issues List */}
              {activeRightTab === 'issues' && (
                <div className="mt-3.5 space-y-3">
                  {/* Category Filter Chips */}
                  <div className="flex items-center gap-1.5 overflow-x-auto pb-1 text-[11px]">
                    {categories.map((cat) => (
                      <button
                        key={cat}
                        onClick={() => setSelectedCategory(cat)}
                        className={`px-2.5 py-1 rounded-md whitespace-nowrap transition-colors ${
                          selectedCategory === cat
                            ? 'bg-slate-900 text-white font-semibold'
                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                        }`}
                      >
                        {cat}
                      </button>
                    ))}
                  </div>

                  {/* Issues Scrollable List */}
                  <div className="space-y-3 max-h-[420px] overflow-y-auto pr-1">
                    {filteredIssues.length === 0 ? (
                      <div className="p-8 text-center text-slate-400 text-xs">
                        No issues detected under this filter category.
                      </div>
                    ) : !visualResult && (
                      filteredIssues.map((issue) => (
                        <IssueCard
                          key={issue.id}
                          issue={issue}
                          sourceText={text}
                          isAuditStale={isAuditVersionStale}
                          selectedReplacementOptionId={selectedReplacementOptions[issue.id]}
                          onReplacementOptionChange={handleReplacementOptionChange}
                          onApplyFix={handleApplySingleFix}
                          isApplyingFix={isApplyingFix}
                        />
                      ))
                    )}
                  </div>
                </div>
              )}

              {/* TAB 2: Diff & Revision */}
              {activeRightTab === 'diff' && (
                <div className="mt-3.5">
                  <DiffViewer
                    originalText={revisionOriginalText ?? text}
                    correctedText={revisionText ?? analysisResult.corrected_text}
                    onApplyAll={handleApplyAllCorrections}
                    complianceApproved={revisionComplianceApproved}
                  />
                </div>
              )}

              {/* TAB 3: Readability & Accessibility */}
              {activeRightTab === 'readability' && analysisResult.readability && (
                <div className="mt-3.5 p-4 rounded-xl bg-slate-50 border border-slate-100 space-y-3 text-xs">
                  <div className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-2">
                    Linguistic Metrics
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div className="p-3 bg-white rounded-lg border border-slate-200">
                      <span className="text-[11px] text-slate-500 block mb-1">Flesch Reading Ease</span>
                      <span className="text-xl font-bold text-emerald-600">
                        {analysisResult.readability.reading_ease}/100
                      </span>
                      <span className="text-[10px] text-slate-400 block mt-0.5">
                        {analysisResult.readability.reading_ease >= 60 ? 'Plain English' : 'Complex'}
                      </span>
                    </div>

                    <div className="p-3 bg-white rounded-lg border border-slate-200">
                      <span className="text-[11px] text-slate-500 block mb-1">Reading Grade Level</span>
                      <span className="text-xl font-bold text-blue-600">
                        Grade {analysisResult.readability.grade_level}
                      </span>
                      <span className="text-[10px] text-slate-400 block mt-0.5">
                        US school grade
                      </span>
                    </div>

                    <div className="p-3 bg-white rounded-lg border border-slate-200">
                      <span className="text-[11px] text-slate-500 block mb-1">Sentence Count</span>
                      <span className="text-base font-semibold text-slate-900">
                        {analysisResult.readability.sentence_count} sentences
                      </span>
                    </div>

                    <div className="p-3 bg-white rounded-lg border border-slate-200">
                      <span className="text-[11px] text-slate-500 block mb-1">Est. Reading Time</span>
                      <span className="text-base font-semibold text-slate-900">
                        {analysisResult.readability.reading_time_sec}s
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="py-20 text-center text-slate-400 text-xs flex flex-col items-center justify-center">
              <Sparkles className="w-8 h-8 text-slate-300 mb-2" />
              <span className="font-semibold text-slate-700">Ready to audit copy</span>
              <span className="text-[11px] mt-1 text-slate-400 max-w-xs">
                Add content above and run an audit against this brand's guidelines.
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Human Review Modal */}
      <ReviewActionModal
        isOpen={isReviewModalOpen}
        onClose={() => setIsReviewModalOpen(false)}
        onSubmit={handleReviewDecision}
        defaultDecision={defaultReviewDecision}
        contentTitle={title}
      />
    </div>
  );
}
