import React, { useState, useEffect, useRef } from 'react';
import { 
  Sparkles, Upload, FileText, CheckCircle2, AlertTriangle, ShieldCheck, 
  RotateCcw, Check, Layers, Clock, AlertCircle, XCircle, Download
} from 'lucide-react';
import { api } from '../api';
import RiskScoreBadge from '../components/RiskScoreBadge';
import IssueCard from '../components/IssueCard';
import DiffViewer from '../components/DiffViewer';
import ReviewActionModal from '../components/ReviewActionModal';

function getAuditPdfFilename(brandName) {
  const safeBrandName = Array.from(String(brandName || 'Brand'), (character) => {
    const codePoint = character.codePointAt(0);
    return codePoint < 32 || codePoint === 127 || /[<>:"/\\|?*]/.test(character)
      ? '_'
      : character;
  }).join('')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '') || 'Brand';
  return `BrandGuard_Audit_Report_${safeBrandName}.pdf`;
}

const CONTENT_DRAFT_STORAGE_PREFIX = 'brandguard.content-analyzer-draft.v1';

function getContentDraftStorageKey(brandId, contentId) {
  if (!Number.isSafeInteger(brandId) || brandId < 1) return null;
  if (
    contentId !== null
    && (!Number.isSafeInteger(contentId) || contentId < 1)
  ) {
    return null;
  }
  return `${CONTENT_DRAFT_STORAGE_PREFIX}:brand:${brandId}:content:${contentId ?? 'new'}`;
}

function readContentDraft(brandId, contentId) {
  const key = getContentDraftStorageKey(brandId, contentId);
  if (!key) return null;
  try {
    const stored = JSON.parse(window.localStorage.getItem(key) || 'null');
    if (
      !stored
      || stored.brandId !== brandId
      || stored.contentId !== contentId
      || typeof stored.text !== 'string'
    ) {
      return null;
    }
    return stored;
  } catch (error) {
    console.warn('Could not restore the Content Analyzer draft:', error);
    return null;
  }
}

function writeContentDraft(draft) {
  const key = getContentDraftStorageKey(draft.brandId, draft.contentId);
  if (!key || typeof draft.text !== 'string') return false;
  try {
    window.localStorage.setItem(key, JSON.stringify({
      brandId: draft.brandId,
      contentId: draft.contentId,
      text: draft.text,
      updatedAt: Date.now(),
    }));
    return true;
  } catch (error) {
    console.warn('Could not save the Content Analyzer draft:', error);
    return false;
  }
}

function removeContentDraft(brandId, contentId) {
  const key = getContentDraftStorageKey(brandId, contentId);
  if (!key) return;
  try {
    window.localStorage.removeItem(key);
  } catch (error) {
    console.warn('Could not clear the Content Analyzer draft:', error);
  }
}

export default function ContentAnalyzer({
  brandId,
  brandName,
  contentToEdit,
  onCancelEdit,
  onContentSaved,
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
  const [pdfDownloadError, setPdfDownloadError] = useState('');
  const [isDownloadingPdf, setIsDownloadingPdf] = useState(false);
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
  const [manualCorrectionForms, setManualCorrectionForms] = useState({});
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
  const manualPreviewRequestRef = useRef(0);
  const pdfDownloadRequestRef = useRef(false);
  const editorDraftTimerRef = useRef(null);
  const pendingEditorDraftRef = useRef(null);
  const skipNextEditorDraftSaveRef = useRef(false);
  const clearedEditorDraftRef = useRef(null);
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
    const previewToDiscard = rewritePreview;
    rewriteRequestRef.current += 1;
    manualPreviewRequestRef.current += 1;
    setRewritePreview(null);
    setRewriteError('');
    setIsRewriting(false);
    if (
      previewToDiscard?.type === 'manual'
      && contentId
      && previewToDiscard.currentVersionId
    ) {
      api.discardManualCorrections(
        contentId,
        brandId,
        previewToDiscard.currentVersionId,
      ).then((response) => {
        if (!response.success) {
          showToast(
            response.error || 'Could not discard the saved manual correction preview.',
            'error',
          );
        }
      });
    }
  };

  useEffect(() => {
    const requestId = ++manualPreviewRequestRef.current;
    rewriteRequestRef.current += 1;
    setRewritePreview(null);
    setRewriteError('');
    setIsRewriting(false);
    const originalText = contentToEdit?.original_text || '';
    const draftContentId = contentToEdit?.id ?? null;
    const nextDraftKey = getContentDraftStorageKey(brandId, draftContentId);
    const pendingDraft = pendingEditorDraftRef.current;
    if (
      pendingDraft
      && getContentDraftStorageKey(
        pendingDraft.brandId,
        pendingDraft.contentId,
      ) !== nextDraftKey
    ) {
      writeContentDraft(pendingDraft);
    }
    skipNextEditorDraftSaveRef.current = true;
    const editorDraft = readContentDraft(brandId, draftContentId);
    const restoredText = editorDraft?.text ?? originalText;
    const restoredAnalysis = contentToEdit?.latest_analysis;
    const restoredVersion = contentToEdit?.current_version || 1;
    const canRestoreAnalysis = Boolean(
      restoredAnalysis
      && restoredAnalysis.version_number === restoredVersion
      && Array.isArray(restoredAnalysis.issues),
    );
    setTitle(contentToEdit?.title || '');
    setContentType(contentToEdit?.content_type || 'Ad Copy');
    setChannel(contentToEdit?.channel || 'Digital Ads');
    setText(restoredText);
    setAnalysisResult(canRestoreAnalysis ? restoredAnalysis : null);
    setAuditedVersion(canRestoreAnalysis ? restoredVersion : null);
    setIsAuditStale(Boolean(editorDraft && editorDraft.text !== originalText));
    setVisualResult(null);
    setVisualFileName('');
    setVisualError('');
    setRevisionOriginalText(canRestoreAnalysis ? originalText : null);
    setRevisionText(
      canRestoreAnalysis
        ? (restoredAnalysis.corrected_text || originalText)
        : null,
    );
    setRevisionComplianceApproved(false);
    setSelectedReplacementOptions({});
    setManualCorrectionForms({});
    setContentId(contentToEdit?.id || null);
    setSavedText(originalText);
    setCurrentVersion(restoredVersion);
    setStatus(contentToEdit?.status || 'draft');
    setIsReviewModalOpen(false);

    if (contentToEdit?.id && brandId) {
      api.getManualCorrectionPreview(contentToEdit.id, brandId).then((response) => {
        if (manualPreviewRequestRef.current !== requestId) return;
        if (!response.success) {
          setRewriteError(
            response.error || 'Could not retrieve the saved manual correction preview.',
          );
          return;
        }
        const previewState = response.preview_state;
        if (!previewState?.correction_count) return;
        setRewritePreview({
          type: 'manual',
          rewrittenText: previewState.corrected_text,
          currentVersionId: response.current_version_id,
          source: {
            brandId,
            text: originalText,
            contentType: contentToEdit?.content_type || 'Ad Copy',
            channel: contentToEdit?.channel || 'Digital Ads',
            currentVersion: contentToEdit?.current_version || 1,
          },
        });
      });
    }
    return () => {
      manualPreviewRequestRef.current += 1;
    };
  }, [brandId, contentToEdit]);

  const draftContentId = contentToEdit?.id ?? contentId ?? null;

  useEffect(() => {
    if (!brandId) return undefined;

    const draft = { brandId, contentId: draftContentId, text };
    const clearedDraft = clearedEditorDraftRef.current;
    if (
      clearedDraft
      && clearedDraft.brandId === draft.brandId
      && clearedDraft.contentId === draft.contentId
      && clearedDraft.text === draft.text
    ) {
      pendingEditorDraftRef.current = null;
      return undefined;
    }
    clearedEditorDraftRef.current = null;

    if (skipNextEditorDraftSaveRef.current) {
      skipNextEditorDraftSaveRef.current = false;
      pendingEditorDraftRef.current = null;
      return undefined;
    }

    pendingEditorDraftRef.current = draft;
    const timerId = window.setTimeout(() => {
      writeContentDraft(draft);
      if (editorDraftTimerRef.current === timerId) {
        editorDraftTimerRef.current = null;
      }
    }, 500);
    editorDraftTimerRef.current = timerId;

    return () => {
      window.clearTimeout(timerId);
      if (editorDraftTimerRef.current === timerId) {
        editorDraftTimerRef.current = null;
      }
    };
  }, [brandId, contentId, contentToEdit, draftContentId, text]);

  useEffect(() => {
    const flushPendingDraft = () => {
      if (editorDraftTimerRef.current !== null) {
        window.clearTimeout(editorDraftTimerRef.current);
        editorDraftTimerRef.current = null;
      }
      if (pendingEditorDraftRef.current) {
        writeContentDraft(pendingEditorDraftRef.current);
      }
    };
    window.addEventListener('pagehide', flushPendingDraft);
    return () => {
      window.removeEventListener('pagehide', flushPendingDraft);
      flushPendingDraft();
    };
  }, []);

  const moveEditorDraftToContent = (nextContentId, draftText) => {
    const previousContentId = contentToEdit?.id ?? contentId ?? null;
    const nextDraft = {
      brandId,
      contentId: nextContentId,
      text: draftText,
    };
    if (editorDraftTimerRef.current !== null) {
      window.clearTimeout(editorDraftTimerRef.current);
      editorDraftTimerRef.current = null;
    }
    if (writeContentDraft(nextDraft)) {
      if (previousContentId !== nextContentId) {
        removeContentDraft(brandId, previousContentId);
      }
    }
    pendingEditorDraftRef.current = nextDraft;
  };

  const clearEditorDraft = (
    nextContentId = contentToEdit?.id ?? contentId ?? null,
    clearedText = text,
  ) => {
    if (editorDraftTimerRef.current !== null) {
      window.clearTimeout(editorDraftTimerRef.current);
      editorDraftTimerRef.current = null;
    }
    pendingEditorDraftRef.current = null;
    const draftContentIds = new Set([
      contentToEdit?.id ?? contentId ?? null,
      nextContentId,
    ]);
    draftContentIds.forEach((draftContentIdToRemove) => {
      removeContentDraft(brandId, draftContentIdToRemove);
    });
    clearedEditorDraftRef.current = {
      brandId,
      contentId: nextContentId,
      text: clearedText,
    };
  };

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
        setManualCorrectionForms({});
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
    if (rewritePreview?.type === 'manual') {
      showToast(
        'Save or discard the manual correction preview before running another audit.',
        'error',
      );
      return;
    }
    manualPreviewRequestRef.current += 1;
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
          if (!contentId) {
            moveEditorDraftToContent(res.content_id, text);
          }
          setContentId(res.content_id);
          onContentSaved?.(res.content_id);
        }
        setSavedText(text);
        setAnalysisResult(res.result);
        setRevisionOriginalText(text);
        setRevisionText(res.result.corrected_text || text);
        setRevisionComplianceApproved(
          res.result.revision_compliance?.approved === true
        );
        setSelectedReplacementOptions({});
        setManualCorrectionForms({});
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

    clearRewritePreview();
    const requestId = ++rewriteRequestRef.current;
    const source = { brandId, text, contentType, channel };
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
    const { rewrittenText, source, type } = rewritePreview;
    const isManualPreview = type === 'manual';
    const isCorrectionPreview = type === 'corrections';
    if (
      source.currentVersion !== undefined
      && source.currentVersion !== currentVersion
    ) {
      clearRewritePreview();
      setRewriteError('The source version changed. Run the audit again before saving this preview.');
      return;
    }
    const confirmation = isManualPreview
      ? 'Save this manually reviewed correction as a new content version? The current version will remain in history.'
      : isCorrectionPreview
        ? 'Save these validated corrections as a new content version? The current version will remain in history.'
        : 'Save this AI rewrite as a new content version? The current version will remain in history.';
    if (!window.confirm(confirmation)) return;

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
        throw new Error(
          res.error
          || (isCorrectionPreview
            ? 'Could not save the corrections as a new version.'
            : 'Could not save the AI rewrite as a new version.'),
        );
      }

      clearEditorDraft(res.content_id, rewrittenText);
      setText(rewrittenText);
      setSavedText(rewrittenText);
      setContentId(res.content_id);
      onContentSaved?.(res.content_id);
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
      showToast(
        isManualPreview
          ? `Saved reviewed correction as version ${res.version || 1}.`
          : isCorrectionPreview
            ? `Saved corrections as version ${res.version || 1}.`
            : `Saved AI rewrite as version ${res.version || 1}.`,
      );
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

  const handleOpenManualEditor = (issue) => {
    setManualCorrectionForms((current) => ({
      ...current,
      [issue.id]: {
        open: true,
        replacementText: issue.replacement_text || issue.suggestion || '',
        error: '',
        isApproving: false,
      },
    }));
  };

  const handleManualReplacementChange = (issueId, replacementText) => {
    setManualCorrectionForms((current) => ({
      ...current,
      [issueId]: {
        ...current[issueId],
        replacementText,
        error: '',
      },
    }));
  };

  const handleCancelManualEditor = (issueId) => {
    setManualCorrectionForms((current) => ({
      ...current,
      [issueId]: {
        ...current[issueId],
        open: false,
        error: '',
      },
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
    return latestContent;
  };

  const handleApproveManualCorrection = async (issue, replacementText) => {
    setManualCorrectionForms((current) => ({
      ...current,
      [issue.id]: {
        ...current[issue.id],
        isApproving: true,
        error: '',
      },
    }));
    try {
      if (!contentId) {
        throw new Error('Run an audit before approving a manual correction.');
      }
      if (savedText !== text) {
        setIsAuditStale(true);
        throw new Error('The source text changed. Run the audit again before approving this correction.');
      }

      const latestContent = await verifyCurrentAuditVersion();
      const versionRecord = latestContent.versions?.find(
        (version) => version.version_number === auditedVersion,
      );
      if (!versionRecord?.id) {
        throw new Error('Could not verify the audited version. Run the audit again.');
      }
      const location = issue.location;
      if (
        !Number.isInteger(location?.start)
        || !Number.isInteger(location?.end)
        || location.start < 0
        || location.end <= location.start
      ) {
        throw new Error('The issue has no valid source span. Run the audit again.');
      }

      const response = await api.approveManualCorrection({
        content_id: contentId,
        brand_id: brandId,
        current_version_id: versionRecord.id,
        issue_id: issue.id,
        original_text: issue.matched_text,
        replacement_text: replacementText,
        source_text: savedText,
        source_start: location.start,
        source_end: location.end,
      });
      if (!response.success) {
        if (response.stale) setIsAuditStale(true);
        throw new Error(response.error || 'The manual correction could not be approved.');
      }
      if (!response.preview_state?.corrected_text) {
        throw new Error('The server did not return a validated correction preview.');
      }

      setRewritePreview({
        type: 'manual',
        rewrittenText: response.preview_state.corrected_text,
        currentVersionId: response.current_version_id,
        source: {
          brandId,
          text: savedText,
          contentType,
          channel,
          currentVersion: auditedVersion,
        },
      });
      setRewriteError('');
      setManualCorrectionForms((current) => ({
        ...current,
        [issue.id]: {
          ...current[issue.id],
          open: false,
          isApproving: false,
          error: '',
        },
      }));
      showToast(
        'Correction added to the preview. It is not compliance-approved or saved yet.',
        'info',
      );
    } catch (error) {
      setManualCorrectionForms((current) => ({
        ...current,
        [issue.id]: {
          ...current[issue.id],
          isApproving: false,
          error: error.message || 'The manual correction could not be approved.',
        },
      }));
    }
  };

  const handleApplySingleFix = async (issue, selectedIssueId = issue.id) => {
    if (isApplyingFixRef.current) return;
    if (!contentId) {
      showToast('Run an audit before applying a correction.', 'error');
      return;
    }
    if (savedText !== text) {
      setIsAuditStale(true);
      showToast('The source text changed. Run the audit again before applying this correction.', 'error');
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
        source_text: savedText,
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
      onContentSaved?.(contentId);
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
    if (savedText !== text) {
      setIsAuditStale(true);
      showToast('The source text changed. Run the audit again before applying corrections.', 'error');
      return;
    }
    if (rewritePreview && rewritePreview.type !== 'corrections') {
      showToast('Save or discard the current preview before adopting all corrections.', 'error');
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
        source_text: savedText,
      });
      if (!res.success) {
        if (
          res.status === 409
          && res.current_version !== undefined
          && res.current_version !== auditedVersion
        ) {
          setIsAuditStale(true);
        }
        throw new Error(res.error || 'Could not prepare the corrections preview.');
      }
      setRewriteError('');
      setRewritePreview({
        type: 'corrections',
        rewrittenText: res.applied_text,
        source: {
          brandId,
          text: savedText,
          contentType,
          channel,
          currentVersion,
        },
      });
      setRevisionOriginalText(savedText);
      setRevisionText(res.applied_text);
      setRevisionComplianceApproved(false);
      setActiveRightTab('diff');
      showToast('Combined corrections are previewed here. Save as a new version to keep them.');
    } catch (err) {
      showToast(err.message || 'Could not prepare the corrections preview.', 'error');
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
      if (!contentId) {
        moveEditorDraftToContent(targetId, text);
      }
      setContentId(targetId);
      onContentSaved?.(targetId);
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

  const handleDownloadPdfReport = async () => {
    if (pdfDownloadRequestRef.current) return;
    if (!contentId || !analysisResult) {
      setPdfDownloadError('Run an audit and save its result before downloading a PDF report.');
      return;
    }
    if (isAuditVersionStale || savedText !== text) {
      setPdfDownloadError('The audit is stale. Run the audit again before downloading its report.');
      return;
    }

    pdfDownloadRequestRef.current = true;
    setIsDownloadingPdf(true);
    setPdfDownloadError('');
    let downloadUrl = null;
    let downloadAnchor = null;
    try {
      const result = await api.downloadAuditReportPdf(contentId, brandId);
      if (!result.success || !result.blob) {
        throw new Error(result.error || 'Could not generate the PDF report.');
      }

      downloadUrl = URL.createObjectURL(result.blob);
      downloadAnchor = document.createElement('a');
      downloadAnchor.href = downloadUrl;
      downloadAnchor.download = getAuditPdfFilename(brandName);
      downloadAnchor.hidden = true;
      document.body.appendChild(downloadAnchor);
      downloadAnchor.click();
    } catch (error) {
      setPdfDownloadError(error.message || 'Could not generate the PDF report.');
    } finally {
      downloadAnchor?.remove();
      if (downloadUrl) {
        window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      }
      pdfDownloadRequestRef.current = false;
      setIsDownloadingPdf(false);
    }
  };

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
                const nextText = e.target.value;
                if (brandId) {
                  pendingEditorDraftRef.current = {
                    brandId,
                    contentId: contentToEdit?.id ?? contentId ?? null,
                    text: nextText,
                  };
                }
                clearRewritePreview();
                setText(nextText);
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
                  onClick={() => {
                    clearEditorDraft();
                    onCancelEdit();
                  }}
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
                    {rewritePreview.type === 'manual'
                      ? 'Manually reviewed correction preview. The original remains unchanged and this is not compliance approval.'
                      : rewritePreview.type === 'corrections'
                        ? 'Validated automatic corrections preview. The original remains unchanged until you save.'
                        : 'Gemini-generated preview. Your original content remains unchanged until you save.'}
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
                      <div className="flex shrink-0 items-center gap-1">
                        <span className={`rounded px-2 py-0.5 text-[10px] font-semibold ${
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
                        {check.severity && (
                          <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
                            {check.severity === 'undetermined'
                              ? 'Severity undetermined'
                              : check.severity === 'none'
                                ? 'No severity'
                                : `${check.severity} severity`}
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="mt-1.5 text-xs leading-relaxed text-slate-600">
                      {check.explanation || check.evidence}
                    </p>
                    {check.recommendation && (
                      <p className="mt-1 text-xs leading-relaxed text-slate-700">
                        <span className="font-semibold">Recommendation:</span> {check.recommendation}
                      </p>
                    )}
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
                <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                  <span className="text-xs font-bold uppercase tracking-wider text-slate-500">
                    Compliance Health
                  </span>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-600">
                      {analysisResult.engine_used === 'AI_GEMINI' ? 'Gemini AI' : 'Rule-Based Fallback'}
                    </span>
                    <button
                      type="button"
                      onClick={handleDownloadPdfReport}
                      disabled={
                        isDownloadingPdf
                        || !contentId
                        || isAuditVersionStale
                        || savedText !== text
                      }
                      className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-600 px-3 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Download className="h-3.5 w-3.5" />
                      <span>
                        {isDownloadingPdf ? 'Generating PDF…' : 'Download PDF Report'}
                      </span>
                    </button>
                  </div>
                </div>
                {pdfDownloadError && (
                  <p
                    role="alert"
                    className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
                  >
                    {pdfDownloadError}
                  </p>
                )}

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
                          onManualCorrection={handleApproveManualCorrection}
                          onOpenManualEditor={handleOpenManualEditor}
                          onCancelManualEditor={handleCancelManualEditor}
                          onManualReplacementChange={handleManualReplacementChange}
                          manualEditorOpen={manualCorrectionForms[issue.id]?.open === true}
                          manualReplacement={
                            manualCorrectionForms[issue.id]?.replacementText || ''
                          }
                          manualCorrectionError={
                            manualCorrectionForms[issue.id]?.error || ''
                          }
                          isApprovingManualCorrection={
                            manualCorrectionForms[issue.id]?.isApproving === true
                          }
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
