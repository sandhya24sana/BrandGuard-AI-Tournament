import React, { useState, useEffect, useRef, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import Dashboard from './pages/Dashboard';
import ContentAnalyzer from './pages/ContentAnalyzer';
import GuidelinesManager from './pages/GuidelinesManager';
import ReviewHistory from './pages/ReviewHistory';
import { api } from './api';
import { Building2, CheckCircle2, Shield } from 'lucide-react';
import CreateBrandModal from './components/CreateBrandModal';

const UI_STATE_STORAGE_KEY = 'brandguard.ui-state.v1';
const APP_TABS = new Set(['dashboard', 'analyzer', 'guidelines', 'reviews']);

function readPersistedUiState() {
  const emptyState = {
    currentTab: 'analyzer',
    selectedBrandId: null,
    activeContentId: null,
    auditIdToInspect: null,
  };
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(UI_STATE_STORAGE_KEY) || '{}',
    );
    const positiveId = (value) => (
      Number.isSafeInteger(value) && value > 0 ? value : null
    );
    return {
      currentTab: APP_TABS.has(stored.currentTab)
        ? stored.currentTab
        : emptyState.currentTab,
      selectedBrandId: positiveId(stored.selectedBrandId),
      activeContentId: positiveId(stored.activeContentId),
      auditIdToInspect: positiveId(stored.auditIdToInspect),
    };
  } catch {
    return emptyState;
  }
}

export default function App() {
  const [initialUiState] = useState(readPersistedUiState);
  const [currentTab, setCurrentTab] = useState(initialUiState.currentTab);
  const [brands, setBrands] = useState([]);
  const [selectedBrandId, setSelectedBrandId] = useState(initialUiState.selectedBrandId);
  const [systemStatus, setSystemStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [brandLoadError, setBrandLoadError] = useState('');
  const [contentRestoreError, setContentRestoreError] = useState('');
  const [uiRestoreReady, setUiRestoreReady] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isCreateBrandOpen, setIsCreateBrandOpen] = useState(false);
  const [brandCreatedMessage, setBrandCreatedMessage] = useState('');
  const brandCreatedTimerRef = useRef(null);
  const [auditIdToInspect, setAuditIdToInspect] = useState(
    initialUiState.currentTab === 'reviews'
      ? initialUiState.auditIdToInspect
      : null,
  );
  const [contentToEdit, setContentToEdit] = useState(null);
  const [activeContentId, setActiveContentId] = useState(
    initialUiState.currentTab === 'analyzer'
      ? initialUiState.activeContentId
      : null,
  );
  const initRequestId = useRef(0);

  const restoreContentById = useCallback(async (contentId, availableBrands) => {
    const detail = await api.getContentDetail(contentId);
    if (!detail.success || !detail.content) {
      throw new Error(detail.error || 'Could not restore the saved content.');
    }
    if (!availableBrands.some((brand) => brand.id === detail.content.brand_id)) {
      throw new Error('The saved content belongs to a brand that is no longer available.');
    }
    return {
      ...detail.content,
      latest_analysis: detail.latest_analysis || null,
    };
  }, []);

  const initData = useCallback(async () => {
    const requestId = ++initRequestId.current;
    try {
      const [brandsRes, statusRes] = await Promise.all([
        api.getBrands(),
        api.getSystemStatus()
      ]);

      if (!brandsRes.success) {
        throw new Error(brandsRes.error || 'Could not load brands.');
      }
      if (!Array.isArray(brandsRes.brands)) {
        throw new Error('The server returned an invalid brand list.');
      }
      if (requestId !== initRequestId.current) return;

      const nextBrands = brandsRes.brands;
      setBrands(nextBrands);
      const preferredBrandId = nextBrands.some(
        (brand) => brand.id === initialUiState.selectedBrandId,
      )
        ? initialUiState.selectedBrandId
        : (nextBrands[0]?.id ?? null);
      setSelectedBrandId(preferredBrandId);
      setBrandLoadError('');

      if (statusRes.success) {
        setSystemStatus(statusRes);
      } else {
        console.error('Could not load system status:', statusRes.error);
      }

      const contentIdToRestore = initialUiState.currentTab === 'analyzer'
        ? initialUiState.activeContentId
        : null;
      if (contentIdToRestore) {
        try {
          const restoredContent = await restoreContentById(
            contentIdToRestore,
            nextBrands,
          );
          if (requestId !== initRequestId.current) return;
          setContentToEdit(restoredContent);
          setActiveContentId(restoredContent.id);
          setSelectedBrandId(restoredContent.brand_id);
          setContentRestoreError('');
        } catch (error) {
          if (requestId !== initRequestId.current) return;
          setContentRestoreError(
            error.message || 'Could not restore the saved content.',
          );
        }
      } else {
        setContentToEdit(null);
        setActiveContentId(null);
      }
      if (requestId === initRequestId.current) {
        setUiRestoreReady(true);
      }
    } catch (err) {
      if (requestId === initRequestId.current) {
        setBrandLoadError(err.message || 'Could not load BrandGuard AI data.');
      }
    } finally {
      if (requestId === initRequestId.current) setLoading(false);
    }
  }, [initialUiState, restoreContentById]);

  useEffect(() => {
    initData();
  }, [initData]);

  useEffect(() => {
    if (!uiRestoreReady) return;
    try {
      window.localStorage.setItem(UI_STATE_STORAGE_KEY, JSON.stringify({
        currentTab,
        selectedBrandId,
        activeContentId: currentTab === 'analyzer'
          ? (activeContentId || contentToEdit?.id || null)
          : null,
        auditIdToInspect: currentTab === 'reviews' ? auditIdToInspect : null,
      }));
    } catch (error) {
      console.warn('Could not save the current workspace selection:', error);
    }
  }, [
    uiRestoreReady,
    currentTab,
    selectedBrandId,
    activeContentId,
    contentToEdit,
    auditIdToInspect,
  ]);

  const openCreateBrand = () => {
    if (brandCreatedTimerRef.current) {
      window.clearTimeout(brandCreatedTimerRef.current);
      brandCreatedTimerRef.current = null;
    }
    setBrandCreatedMessage('');
    setIsCreateBrandOpen(true);
  };

  const refreshBrands = async (preferredBrandId = selectedBrandId) => {
    const res = await api.getBrands();
    if (!res.success) throw new Error(res.error || 'Could not refresh brands.');
    if (!Array.isArray(res.brands)) {
      throw new Error('The server returned an invalid brand list.');
    }
    const nextBrands = res.brands;
    setBrands(nextBrands);
    setSelectedBrandId(
      nextBrands.some((brand) => brand.id === preferredBrandId)
        ? preferredBrandId
        : (nextBrands[0]?.id ?? null)
    );
  };

  const handleCreateBrand = async (brandData) => {
    const result = await api.createBrand(brandData);
    if (!result.success) throw new Error(result.error || 'Could not create brand.');
    await refreshBrands(result.brand_id);
    setCurrentTab('guidelines');
    setBrandCreatedMessage('Brand created successfully.');
    if (brandCreatedTimerRef.current) window.clearTimeout(brandCreatedTimerRef.current);
    brandCreatedTimerRef.current = window.setTimeout(() => {
      setBrandCreatedMessage('');
      brandCreatedTimerRef.current = null;
    }, 3500);
  };

  const handleDeleteBrand = async () => {
    setContentToEdit(null);
    setActiveContentId(null);
    await refreshBrands(null);
  };

  const handleEditContent = (content) => {
    if (
      !content
      || !Number.isInteger(content.id)
      || content.id < 1
      || !Number.isInteger(content.brand_id)
      || content.brand_id < 1
    ) {
      return;
    }

    setContentToEdit(content);
    setActiveContentId(content.id);
    setAuditIdToInspect(null);
    setContentRestoreError('');
    setSelectedBrandId(content.brand_id);
    setCurrentTab('analyzer');
  };

  const handleFinishContentEdit = () => {
    setContentToEdit(null);
    setActiveContentId(null);
    setCurrentTab('reviews');
  };

  const handleNewScan = () => {
    setContentToEdit(null);
    setActiveContentId(null);
    setAuditIdToInspect(null);
    setContentRestoreError('');
    setCurrentTab('analyzer');
  };

  const handleTabChange = (tab) => {
    setContentToEdit(null);
    setActiveContentId(null);
    if (tab !== 'reviews') setAuditIdToInspect(null);
    setContentRestoreError('');
    setCurrentTab(tab);
  };

  const handleInspectAudit = (auditId) => {
    if (!Number.isInteger(auditId) || auditId < 1) return;
    setContentToEdit(null);
    setActiveContentId(null);
    setAuditIdToInspect(auditId);
    setCurrentTab('reviews');
  };

  const handleBrandSelection = (brandId) => {
    if (brandId !== selectedBrandId) {
      setContentToEdit(null);
      setActiveContentId(null);
      setAuditIdToInspect(null);
      setContentRestoreError('');
    }
    setSelectedBrandId(brandId);
  };

  const handleContentSaved = (savedContentId) => {
    if (Number.isSafeInteger(savedContentId) && savedContentId > 0) {
      setActiveContentId(savedContentId);
      setContentRestoreError('');
    }
  };

  const handleRetryContentRestore = async () => {
    if (!activeContentId) return;
    setContentRestoreError('');
    try {
      const restoredContent = await restoreContentById(activeContentId, brands);
      setContentToEdit(restoredContent);
      setActiveContentId(restoredContent.id);
      setSelectedBrandId(restoredContent.brand_id);
    } catch (error) {
      setContentRestoreError(
        error.message || 'Could not restore the saved content.',
      );
    }
  };

  const clearAuditToInspect = useCallback((handledAuditId) => {
    setAuditIdToInspect((currentAuditId) => (
      currentAuditId === handledAuditId ? null : currentAuditId
    ));
  }, []);

  return (
    <div className="min-h-screen bg-[#f8fafc] text-slate-900 flex font-sans antialiased">
      {/* Left Sidebar */}
      <Sidebar
        currentTab={currentTab}
        setCurrentTab={handleTabChange}
        systemStatus={systemStatus}
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        onNewScan={handleNewScan}
      />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 lg:pl-64">
        {/* Top Header */}
        <Header
          brands={brands}
          selectedBrandId={selectedBrandId}
          setSelectedBrandId={handleBrandSelection}
          onOpenSidebar={() => setIsSidebarOpen(true)}
          onNewScan={handleNewScan}
          currentTab={currentTab}
          onCreateBrand={openCreateBrand}
        />

        {/* Page View Container */}
        <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 lg:p-8">
          {contentRestoreError && !loading && !brandLoadError && (
            <div
              role="alert"
              className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900"
            >
              <span>{contentRestoreError}</span>
              <button
                onClick={handleRetryContentRestore}
                className="rounded-md border border-amber-300 bg-white px-3 py-1.5 font-semibold hover:bg-amber-100"
              >
                Retry content restore
              </button>
            </div>
          )}
          {loading ? (
            <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-3">
              <div className="w-8 h-8 border-3 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
              <p className="text-xs text-slate-500 font-medium">Loading BrandGuard AI...</p>
            </div>
          ) : brandLoadError ? (
            <div role="alert" className="min-h-[50vh] flex flex-col items-center justify-center gap-3 text-center">
              <p className="text-sm text-rose-700">{brandLoadError}</p>
              <button
                onClick={() => {
                  setLoading(true);
                  setBrandLoadError('');
                  initData();
                }}
                className="px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-700 bg-white border border-slate-200 hover:bg-slate-50"
              >
                Retry
              </button>
            </div>
          ) : (
            <>
              {brands.length === 0 ? (
                <div className="min-h-[55vh] flex items-center justify-center">
                  <div className="max-w-lg w-full bg-white rounded-2xl border border-slate-200 p-8 text-center shadow-xs">
                    <div className="mx-auto w-12 h-12 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center border border-blue-100">
                      <Building2 className="w-6 h-6" />
                    </div>
                    <h2 className="mt-4 text-lg font-bold text-slate-900">Create your first brand</h2>
                    <p className="mt-2 text-sm text-slate-500">
                      Add a brand profile and then define its approved facts, voice, terminology, and compliance rules.
                    </p>
                    <button
                      onClick={openCreateBrand}
                      className="mt-5 inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700"
                    >
                      <Building2 className="w-4 h-4" />
                      Create Brand
                    </button>
                  </div>
                </div>
              ) : (
                <>
              {currentTab === 'dashboard' && (
                <Dashboard
                  brandId={selectedBrandId}
                  brands={brands}
                  onNavigateToAnalyzer={handleNewScan}
                  onNavigateToGuidelines={() => setCurrentTab('guidelines')}
                  onInspectAudit={handleInspectAudit}
                />
              )}

              {currentTab === 'analyzer' && (
                <ContentAnalyzer
                  brandId={selectedBrandId}
                  brandName={brands.find((brand) => brand.id === selectedBrandId)?.name}
                  contentToEdit={contentToEdit}
                  onCancelEdit={handleFinishContentEdit}
                  onContentSaved={handleContentSaved}
                />
              )}

              {currentTab === 'guidelines' && (
                <GuidelinesManager
                  brandId={selectedBrandId}
                  onBrandUpdated={() => refreshBrands()}
                  onDeleteBrand={handleDeleteBrand}
                />
              )}

              {currentTab === 'reviews' && (
                <ReviewHistory
                  brandId={selectedBrandId}
                  initialContentId={auditIdToInspect}
                  onInitialContentHandled={clearAuditToInspect}
                  onEditContent={handleEditContent}
                />
              )}
                </>
              )}
            </>
          )}
        </main>

        {/* Minimal Clean Footer */}
        <footer className="border-t border-slate-200 bg-white py-4 px-6 text-xs text-slate-500">
          <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Shield className="w-3.5 h-3.5 text-blue-600" />
              <span className="font-semibold text-slate-800">BrandGuard AI</span>
              <span>— Intelligent Brand Governance Platform</span>
            </div>

            <div className="flex items-center gap-3 text-[11px] text-slate-400">
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
                {systemStatus?.mode || 'Governance Engine Live'}
              </span>
              <span>•</span>
              <span>React.js + Python Flask + SQLite</span>
            </div>
          </div>
        </footer>
      </div>
      <CreateBrandModal
        isOpen={isCreateBrandOpen}
        onClose={() => setIsCreateBrandOpen(false)}
        onSubmit={handleCreateBrand}
      />
      {brandCreatedMessage && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs font-semibold text-emerald-800 shadow-md"
        >
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
          <span>{brandCreatedMessage}</span>
        </div>
      )}
    </div>
  );
}
