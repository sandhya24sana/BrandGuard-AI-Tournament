import React, { useState, useEffect, useRef } from 'react';
import { 
  CheckSquare, CheckCircle2, AlertTriangle, RotateCcw, Clock, Pencil,
  Download, Eye, X, ChevronRight, XCircle 
} from 'lucide-react';
import { api } from '../api';
import DiffViewer from '../components/DiffViewer';

function getSavedVersions(detail) {
  return (Array.isArray(detail?.versions) ? detail.versions : [])
    .filter((version) => (
      Number.isInteger(version.version_number) &&
      version.version_number > 0 &&
      typeof version.text === 'string'
    ))
    .sort((left, right) => left.version_number - right.version_number);
}

function formatVersionDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Timestamp unavailable' : date.toLocaleString();
}

export default function ReviewHistory({
  brandId,
  initialContentId,
  onInitialContentHandled,
  onEditContent
}) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [detailModalOpen, setDetailModalOpen] = useState(false);
  const [activeModalTab, setActiveModalTab] = useState('versions');
  const [detailError, setDetailError] = useState('');
  const [comparisonLeftVersion, setComparisonLeftVersion] = useState('');
  const [comparisonRightVersion, setComparisonRightVersion] = useState('');
  const detailRequestId = useRef(0);

  const selectContentDetail = (detail) => {
    const versions = getSavedVersions(detail).sort(
      (left, right) => right.version_number - left.version_number
    );
    setSelectedDetail(detail);
    setComparisonLeftVersion(
      versions.length > 1 ? String(versions[1].version_number) : ''
    );
    setComparisonRightVersion(
      versions.length > 0 ? String(versions[0].version_number) : ''
    );
  };

  useEffect(() => {
    let active = true;
    setLoading(true);
    setItems([]);
    setListError('');

    const loadItems = async () => {
      try {
        const res = await api.getContentList(brandId, statusFilter === 'ALL' ? null : statusFilter);
        if (!active) return;
        if (!res.success) throw new Error(res.error || 'Could not load review history.');
        setItems(Array.isArray(res.items) ? res.items : []);
      } catch (err) {
        if (active) {
          setItems([]);
          setListError(err.message || 'Could not load review history.');
        }
      } finally {
        if (active) setLoading(false);
      }
    };
    loadItems();
    return () => {
      active = false;
    };
  }, [brandId, statusFilter]);

  const handleViewDetail = async (contentId) => {
    const requestId = ++detailRequestId.current;
    setDetailError('');
    try {
      const res = await api.getContentDetail(contentId, brandId);
      if (requestId !== detailRequestId.current) return;
      if (!res.success) throw new Error(res.error || 'Could not load audit details.');
      if (brandId && res.content?.brand_id !== brandId) {
        throw new Error('This content does not belong to the selected brand.');
      }
      selectContentDetail(res);
      setActiveModalTab('versions');
      setDetailModalOpen(true);
    } catch (err) {
      if (requestId === detailRequestId.current) {
        setDetailError(err.message || 'Could not load audit details.');
      }
    }
  };

  const handleEditContent = (content) => {
    if (!onEditContent) return;
    setDetailModalOpen(false);
    onEditContent(content);
  };

  useEffect(() => {
    detailRequestId.current += 1;
    setSelectedDetail(null);
    setComparisonLeftVersion('');
    setComparisonRightVersion('');
    setDetailModalOpen(false);
    setDetailError('');
  }, [brandId]);

  useEffect(() => {
    if (!initialContentId) return undefined;
    let active = true;
    const requestId = ++detailRequestId.current;

    const loadInitialDetail = async () => {
      setDetailError('');
      try {
        const res = await api.getContentDetail(initialContentId, brandId);
        if (!active || requestId !== detailRequestId.current) return;
        if (!res.success) throw new Error(res.error || 'Could not load audit details.');
        if (brandId && res.content?.brand_id !== brandId) {
          throw new Error('This content does not belong to the selected brand.');
        }
        selectContentDetail(res);
        setActiveModalTab('versions');
        setDetailModalOpen(true);
      } catch (err) {
        if (active && requestId === detailRequestId.current) {
          setDetailError(err.message || 'Could not load audit details.');
        }
      } finally {
        if (active && requestId === detailRequestId.current) {
          onInitialContentHandled?.(initialContentId);
        }
      }
    };

    loadInitialDetail();
    return () => {
      active = false;
    };
  }, [brandId, initialContentId, onInitialContentHandled]);

  const handleExportReport = async (contentId) => {
    try {
      const res = await api.exportAuditReport(contentId, brandId);
      if (!res.success) throw new Error(res.error || 'Could not export the audit report.');
      const reportBlob = new Blob([JSON.stringify(res.report, null, 2)], {
        type: 'application/json',
      });
      const downloadUrl = URL.createObjectURL(reportBlob);
      const downloadAnchor = document.createElement('a');
      try {
        downloadAnchor.setAttribute('href', downloadUrl);
        downloadAnchor.setAttribute('download', `brandguard-audit-${contentId}-${Date.now()}.json`);
        document.body.appendChild(downloadAnchor);
        downloadAnchor.click();
      } finally {
        downloadAnchor.remove();
        URL.revokeObjectURL(downloadUrl);
      }
    } catch (err) {
      setDetailError(err.message || 'Could not export the audit report.');
    }
  };

  const getStatusBadge = (status) => {
    switch (status) {
      case 'approved':
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200"><CheckCircle2 className="w-3 h-3" /> Approved</span>;
      case 'rejected':
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-50 text-rose-700 border border-rose-200"><XCircle className="w-3 h-3" /> Rejected</span>;
      case 'changes_requested':
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200"><RotateCcw className="w-3 h-3" /> Changes Requested</span>;
      case 'pending_review':
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200"><Clock className="w-3 h-3" /> Pending Review</span>;
      default:
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600">Draft</span>;
    }
  };

  const getScoreBadge = (score) => {
    const color = score >= 90 ? 'text-emerald-700 bg-emerald-50 border-emerald-200' :
                  score >= 75 ? 'text-amber-700 bg-amber-50 border-amber-200' :
                  'text-rose-700 bg-rose-50 border-rose-200';
    return (
      <span className={`px-2 py-0.5 rounded border font-mono font-semibold text-xs ${color}`}>
        {score}/100
      </span>
    );
  };

  const savedVersions = getSavedVersions(selectedDetail);
  const leftVersion = savedVersions.find(
    (version) => String(version.version_number) === comparisonLeftVersion
  );
  const rightVersion = savedVersions.find(
    (version) => String(version.version_number) === comparisonRightVersion
  );

  return (
    <div className="space-y-4">
      {/* Header & Filter Controls */}
      <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
            <CheckSquare className="w-4 h-4 text-blue-600" />
            Audit Trail & Revision History
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Verified review decisions, content revisions, and downloadable compliance records.
          </p>
        </div>

        {/* Filter buttons */}
        <div className="flex items-center gap-1 overflow-x-auto text-xs font-medium bg-slate-100 p-1 rounded-lg">
          {['ALL', 'approved', 'changes_requested', 'rejected', 'pending_review', 'draft'].map((st) => (
            <button
              key={st}
              onClick={() => setStatusFilter(st)}
              className={`px-3 py-1.5 rounded-md whitespace-nowrap transition-colors capitalize ${
                statusFilter === st
                  ? 'bg-white text-slate-900 font-semibold shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              {st.replace('_', ' ')}
            </button>
          ))}
        </div>
      </div>

      {detailError && (
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-700">
          {detailError}
        </div>
      )}

      {/* Content Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-xs overflow-hidden">
        {loading ? (
          <div className="p-12 text-center text-slate-400 text-xs">Loading review history...</div>
        ) : listError ? (
          <div role="alert" className="p-8 text-center text-rose-700 text-xs">{listError}</div>
        ) : items.length === 0 ? (
          <div className="p-12 text-center text-slate-400 text-xs">
            No content submissions found under this filter. Run an audit in the Content Analyzer to populate.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase tracking-wider text-[10px]">
                  <th className="py-2.5 px-4 font-semibold">Title</th>
                  <th className="py-2.5 px-4 font-semibold">Format / Channel</th>
                  <th className="py-2.5 px-4 font-semibold">Score</th>
                  <th className="py-2.5 px-4 font-semibold">Status</th>
                  <th className="py-2.5 px-4 font-semibold">Version</th>
                  <th className="py-2.5 px-4 font-semibold">Date</th>
                  <th className="py-2.5 px-4 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => (
                  <tr key={item.id} className="hover:bg-slate-50/70 transition-colors">
                    <td className="py-3 px-4 font-medium text-slate-900 max-w-xs truncate">
                      {item.title}
                    </td>
                    <td className="py-3 px-4 text-slate-600">
                      <div>{item.content_type}</div>
                      <div className="text-[10px] text-slate-400">{item.channel}</div>
                    </td>
                    <td className="py-3 px-4">
                      {getScoreBadge(item.risk_score)}
                    </td>
                    <td className="py-3 px-4">
                      {getStatusBadge(item.status)}
                    </td>
                    <td className="py-3 px-4 font-mono text-slate-500">
                      v{item.current_version}
                    </td>
                    <td className="py-3 px-4 text-slate-400">
                      {new Date(item.updated_at).toLocaleDateString()}
                    </td>
                    <td className="py-3 px-4 text-right space-x-1.5 whitespace-nowrap">
                      <button
                        onClick={() => handleViewDetail(item.id)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-slate-50 hover:bg-slate-100 text-slate-700 border border-slate-200 transition-colors"
                        title="View Revisions & Decision Trail"
                      >
                        <Eye className="w-3 h-3 text-slate-500" />
                        <span>Audit Log</span>
                      </button>

                      <button
                        onClick={() => handleEditContent(item)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-slate-50 hover:bg-slate-100 text-slate-700 border border-slate-200 transition-colors"
                        title="Edit and resubmit as a new version"
                      >
                        <Pencil className="w-3 h-3 text-slate-500" />
                        <span>Edit</span>
                      </button>

                      <button
                        onClick={() => handleExportReport(item.id)}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-slate-50 hover:bg-slate-100 text-slate-700 border border-slate-200 transition-colors"
                        title="Download JSON Report"
                      >
                        <Download className="w-3 h-3 text-slate-500" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* DETAIL MODAL: Versions & Decisions */}
      {detailModalOpen && selectedDetail && (!brandId || selectedDetail.content?.brand_id === brandId) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs animate-fadeIn">
          <div className="bg-white border border-slate-200 rounded-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden shadow-xl">
            {/* Modal Header */}
            <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                  <CheckSquare className="w-4 h-4 text-blue-600" />
                  {selectedDetail.content.title}
                </h3>
                <div className="flex items-center gap-2 mt-1 text-xs text-slate-500">
                  <span>Current: v{selectedDetail.content.current_version}</span>
                  <span>•</span>
                  <span>{getStatusBadge(selectedDetail.content.status)}</span>
                  <span>•</span>
                  <span>Score: {getScoreBadge(selectedDetail.content.risk_score)}</span>
                </div>
              </div>
              <button
                onClick={() => setDetailModalOpen(false)}
                className="p-1 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Modal Navigation Tabs */}
            <div className="flex items-center gap-2 px-6 pt-3 border-b border-slate-100 text-xs font-semibold">
              <button
                onClick={() => setActiveModalTab('versions')}
                className={`pb-2.5 border-b-2 transition-all ${
                  activeModalTab === 'versions'
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-slate-500 hover:text-slate-900'
                }`}
              >
                Version Timeline ({selectedDetail.versions?.length || 0})
              </button>
              <button
                onClick={() => setActiveModalTab('reviews')}
                className={`pb-2.5 border-b-2 transition-all ${
                  activeModalTab === 'reviews'
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-slate-500 hover:text-slate-900'
                }`}
              >
                Review Decisions ({selectedDetail.reviews?.length || 0})
              </button>
              <button
                onClick={() => setActiveModalTab('diff')}
                className={`pb-2.5 border-b-2 transition-all ${
                  activeModalTab === 'diff'
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-slate-500 hover:text-slate-900'
                }`}
              >
                Original vs Corrected Diff
              </button>
              <button
                onClick={() => setActiveModalTab('compare')}
                className={`pb-2.5 border-b-2 transition-all ${
                  activeModalTab === 'compare'
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-slate-500 hover:text-slate-900'
                }`}
              >
                Compare Versions
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-6 overflow-y-auto flex-1 space-y-4">
              {/* TAB: Versions */}
              {activeModalTab === 'versions' && (
                <div className="space-y-3">
                  {(selectedDetail.versions || []).map((ver) => (
                    <div key={ver.id} className="p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-xs text-slate-900">Version {ver.version_number}</span>
                        <span className="text-[11px] text-slate-400">{new Date(ver.created_at).toLocaleString()}</span>
                      </div>
                      <div className="p-3 bg-white rounded-lg border border-slate-200 text-xs text-slate-700 font-mono whitespace-pre-wrap">
                        {ver.text}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* TAB: Reviews */}
              {activeModalTab === 'reviews' && (
                <div className="space-y-3">
                  {(selectedDetail.reviews || []).length === 0 ? (
                    <div className="p-6 text-center text-slate-400 text-xs">No review decisions recorded yet.</div>
                  ) : (
                    (selectedDetail.reviews || []).map((rev) => (
                      <div key={rev.id} className="p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-2">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-1.5">
                            <span className="font-semibold text-xs text-slate-900">{rev.reviewer_name}</span>
                            <span className="text-[11px] text-slate-500">({rev.reviewer_role})</span>
                          </div>
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                            rev.decision === 'approved' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' :
                            rev.decision === 'rejected' ? 'bg-rose-50 text-rose-700 border border-rose-200' : 'bg-amber-50 text-amber-700 border border-amber-200'
                          }`}>
                            {rev.decision.replace('_', ' ')}
                          </span>
                        </div>
                        {rev.comments && (
                          <p className="text-xs text-slate-700 bg-white p-2.5 rounded-lg border border-slate-200">
                            "{rev.comments}"
                          </p>
                        )}
                        <span className="text-[10px] text-slate-400 block">{new Date(rev.created_at).toLocaleString()}</span>
                      </div>
                    ))
                  )}
                </div>
              )}

              {/* TAB: Diff */}
              {activeModalTab === 'diff' && (
                <DiffViewer
                  originalText={selectedDetail.content.original_text}
                  correctedText={selectedDetail.content.corrected_text}
                />
              )}

              {activeModalTab === 'compare' && (
                <section aria-label="Saved version comparison" className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <label className="block text-xs font-semibold text-slate-700">
                      First saved version
                      <select
                        aria-label="First saved version"
                        value={comparisonLeftVersion}
                        onChange={(event) => setComparisonLeftVersion(event.target.value)}
                        className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-normal text-slate-700"
                      >
                        <option value="">Select a version</option>
                        {savedVersions.map((version) => (
                          <option key={version.version_number} value={version.version_number}>
                            Version {version.version_number} - {formatVersionDate(version.created_at)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block text-xs font-semibold text-slate-700">
                      Second saved version
                      <select
                        aria-label="Second saved version"
                        value={comparisonRightVersion}
                        onChange={(event) => setComparisonRightVersion(event.target.value)}
                        className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-normal text-slate-700"
                      >
                        <option value="">Select a version</option>
                        {savedVersions.map((version) => (
                          <option key={version.version_number} value={version.version_number}>
                            Version {version.version_number} - {formatVersionDate(version.created_at)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  {savedVersions.length < 2 ? (
                    <div role="status" className="p-6 text-center text-slate-500 text-xs">
                      {savedVersions.length === 0
                        ? 'No saved versions are available to compare.'
                        : 'At least two saved versions are required for comparison.'}
                    </div>
                  ) : comparisonLeftVersion && comparisonRightVersion &&
                    comparisonLeftVersion === comparisonRightVersion ? (
                    <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
                      Select two different saved versions to compare.
                    </div>
                  ) : !leftVersion || !rightVersion ? (
                    <div role="status" className="p-6 text-center text-slate-500 text-xs">
                      Select two saved versions to compare.
                    </div>
                  ) : (
                    <>
                      {leftVersion.text === rightVersion.text && (
                        <div role="status" className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-xs text-blue-800">
                          These versions contain identical text.
                        </div>
                      )}
                      <DiffViewer
                        heading="Saved Version Comparison"
                        originalLabel={`Version ${leftVersion.version_number} - ${formatVersionDate(leftVersion.created_at)}`}
                        correctedLabel={`Version ${rightVersion.version_number} - ${formatVersionDate(rightVersion.created_at)}`}
                        originalText={leftVersion.text}
                        correctedText={rightVersion.text}
                        showComplianceStatus={false}
                      />
                    </>
                  )}
                </section>
              )}
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-3 border-t border-slate-100 bg-slate-50 flex items-center justify-between">
              <button
                onClick={() => handleExportReport(selectedDetail.content.id)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-white hover:bg-slate-100 text-slate-700 border border-slate-200 transition-colors shadow-xs"
              >
                <Download className="w-3.5 h-3.5 text-slate-600" />
                <span>Export JSON Record</span>
              </button>
              <button
                onClick={() => setDetailModalOpen(false)}
                className="px-4 py-1.5 rounded-lg text-xs font-semibold bg-slate-200 text-slate-700 hover:bg-slate-300 transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
