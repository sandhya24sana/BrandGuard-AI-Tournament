import React, { useState, useEffect, useRef, useCallback } from 'react';
import { 
  BookOpen, Plus, Trash2, Palette, AlertOctagon, Scale, 
  Save, RefreshCw, SlidersHorizontal, CheckCircle2, AlertCircle
} from 'lucide-react';
import { api } from '../api';
import GuidelineModal from '../components/GuidelineModal';

const colorsToText = (colors = [], includeReason = false) => colors.map((color) => (
  `${color.name || ''}: ${color.hex || ''}${includeReason && color.reason ? ` | ${color.reason}` : ''}`
)).join('\n');

const parseColors = (value, includeReason = false) => value.split('\n').filter((line) => line.trim()).map((line) => {
  const [colorPart, reason = ''] = includeReason ? line.split('|', 2) : [line, ''];
  const separator = colorPart.indexOf(':');
  if (separator < 1) throw new Error('Use "Color name: #hex" for each palette entry.');
  const name = colorPart.slice(0, separator).trim();
  const hex = colorPart.slice(separator + 1).trim();
  if (!name || !/^#[0-9a-f]{3}(?:[0-9a-f]{3})?$/i.test(hex)) {
    throw new Error('Each color needs a name and a valid 3- or 6-digit hex value.');
  }
  return { name, hex, ...(includeReason ? { reason: reason.trim() } : {}) };
}).filter((color) => color.name);

export default function GuidelinesManager({ brandId, onBrandUpdated, onDeleteBrand }) {
  const [brandData, setBrandData] = useState(null);
  const [loadedBrandId, setLoadedBrandId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('facts');
  const [modalType, setModalType] = useState(null);
  const [toastMessage, setToastMessage] = useState(null);
  const [errorMessage, setErrorMessage] = useState('');
  const loadRequestId = useRef(0);

  // Editable brand profile
  const [editBrand, setEditBrand] = useState({
    name: '',
    tagline: '',
    primary_tone: '',
    target_audience: '',
    voice_traits: [],
    industry: '',
    description: ''
  });

  // Editable visual rules
  const [logoRules, setLogoRules] = useState('');
  const [fontFamily, setFontFamily] = useState('');
  const [contrastMin, setContrastMin] = useState('');
  const [primaryColorsText, setPrimaryColorsText] = useState('');
  const [secondaryColorsText, setSecondaryColorsText] = useState('');
  const [forbiddenColorsText, setForbiddenColorsText] = useState('');

  const showToast = (msg, type = 'success') => {
    setToastMessage({ msg, type });
    setErrorMessage('');
    setTimeout(() => setToastMessage(null), 3000);
  };

  const loadBrandDetails = useCallback(async () => {
    const requestId = ++loadRequestId.current;
    const requestedBrandId = brandId;
    setLoading(true);
    setErrorMessage('');
    try {
      const res = await api.getBrandDetails(requestedBrandId);
      if (requestId !== loadRequestId.current) return;
      if (res.success) {
        setBrandData(res);
        setLoadedBrandId(requestedBrandId);
        setEditBrand({
          name: res.brand.name || '',
          tagline: res.brand.tagline || '',
          primary_tone: res.brand.primary_tone || '',
          voice_traits: res.brand.voice_traits || [],
          target_audience: res.brand.target_audience || '',
          industry: res.brand.industry || '',
          description: res.brand.description || ''
        });
        if (res.visual_rules) {
          setLogoRules(res.visual_rules.logo_rules || '');
          setFontFamily(res.visual_rules.font_family || '');
          setContrastMin(res.visual_rules.contrast_minimum || '');
          setPrimaryColorsText(colorsToText(res.visual_rules.primary_colors || []));
          setSecondaryColorsText(colorsToText(res.visual_rules.secondary_colors || []));
          setForbiddenColorsText(colorsToText(res.visual_rules.forbidden_colors || [], true));
        }
      } else {
        setErrorMessage(res.error || 'Could not load brand guidelines.');
      }
    } catch (err) {
      if (requestId === loadRequestId.current) {
        setErrorMessage(err.message || 'Could not load brand guidelines.');
      }
    } finally {
      if (requestId === loadRequestId.current) {
        setLoading(false);
      }
    }
  }, [brandId]);

  useEffect(() => {
    if (brandId) {
      loadBrandDetails();
    } else {
      setBrandData(null);
      setLoadedBrandId(null);
      setLoading(false);
    }
    return () => {
      loadRequestId.current += 1;
    };
  }, [brandId, loadBrandDetails]);

  const handleSaveBrandInfo = async (e) => {
    e.preventDefault();
    try {
      const res = await api.updateBrand(brandId, editBrand);
      if (!res.success) throw new Error(res.error || 'Could not save the brand profile.');
      showToast('Brand profile updated.');
      if (onBrandUpdated) await onBrandUpdated();
    } catch (err) {
      showToast(err.message || 'Could not save the brand profile.', 'error');
    }
  };

  const handleSaveVisualRules = async () => {
    try {
      const res = await api.updateVisualRules(brandId, {
        primary_colors: parseColors(primaryColorsText),
        secondary_colors: parseColors(secondaryColorsText),
        forbidden_colors: parseColors(forbiddenColorsText, true),
        logo_rules: logoRules,
        font_family: fontFamily,
        contrast_minimum: contrastMin
      });
      if (!res.success) throw new Error(res.error || 'Could not save visual guidelines.');
      showToast('Visual rules updated.');
      await loadBrandDetails();
    } catch (err) {
      showToast(err.message || 'Could not save visual guidelines.', 'error');
    }
  };

  const handleDeleteFact = async (factId) => {
    if (!confirm('Are you sure you want to delete this fact?')) return;
    try {
      const res = await api.deleteFact(factId, brandId);
      if (!res.success) throw new Error(res.error || 'Could not delete the fact.');
      showToast('Fact deleted.');
      await loadBrandDetails();
    } catch (err) {
      showToast(err.message || 'Could not delete the fact.', 'error');
    }
  };

  const handleDeleteTerminology = async (termId) => {
    if (!confirm('Are you sure you want to delete this terminology rule?')) return;
    try {
      const res = await api.deleteTerminology(termId, brandId);
      if (!res.success) throw new Error(res.error || 'Could not delete the terminology rule.');
      showToast('Terminology rule deleted.');
      await loadBrandDetails();
    } catch (err) {
      showToast(err.message || 'Could not delete the terminology rule.', 'error');
    }
  };

  const handleDeleteClaim = async (claimId) => {
    if (!confirm('Are you sure you want to delete this claim rule?')) return;
    try {
      const res = await api.deleteClaimRule(claimId, brandId);
      if (!res.success) throw new Error(res.error || 'Could not delete the claim rule.');
      showToast('Claim rule deleted.');
      await loadBrandDetails();
    } catch (err) {
      showToast(err.message || 'Could not delete the claim rule.', 'error');
    }
  };

  const handleModalSubmit = async (data) => {
    let response;
    if (modalType === 'fact') {
      response = await api.addFact(brandId, data);
    } else if (modalType === 'terminology') {
      response = await api.addTerminology(brandId, data);
    } else if (modalType === 'claim') {
      response = await api.addClaimRule(brandId, data);
    }
    if (!response?.success) throw new Error(response?.error || 'Could not save guideline.');
    await loadBrandDetails();
    showToast('Guideline added.');
  };

  const handleDeleteBrand = async () => {
    const confirmed = window.confirm(
      `Delete "${brandData.brand.name}" and all of its guidelines, content, analyses, reviews, and history? This cannot be undone.`
    );
    if (!confirmed) return;
    try {
      const result = await api.deleteBrand(brandId);
      if (!result.success) throw new Error(result.error || 'Could not delete brand.');
      await onDeleteBrand?.();
    } catch (err) {
      showToast(err.message || 'Could not delete brand.', 'error');
    }
  };

  if (loading || (brandData && loadedBrandId !== brandId && !errorMessage)) {
    return (
      <div className="p-12 text-center text-slate-400 text-xs">
        Loading brand guidelines...
      </div>
    );
  }
  if (!brandData || loadedBrandId !== brandId) {
    return (
      <div className="space-y-3 p-8 text-center">
        <p role="alert" className="text-xs text-rose-700">
          {errorMessage || 'Could not load brand guidelines.'}
        </p>
        <button
          onClick={loadBrandDetails}
          className="px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-700 bg-white border border-slate-200 hover:bg-slate-50"
        >
          Retry
        </button>
      </div>
    );
  }

  const { brand, facts, terminology, visual_rules, claim_rules } = brandData;

  return (
    <div className="space-y-4">
      {/* Toast Alert */}
      {toastMessage && (
        <div className={`fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl border text-xs font-semibold shadow-md flex items-center gap-2 ${
          toastMessage.type === 'error' ? 'border-rose-200 bg-rose-50 text-rose-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'
        }`}>
          {toastMessage.type === 'error'
            ? <AlertCircle className="w-4 h-4 text-rose-600" />
            : <CheckCircle2 className="w-4 h-4 text-emerald-600" />}
          <span>{toastMessage.msg}</span>
        </div>
      )}
      {errorMessage && <p role="alert" className="text-xs text-rose-700">{errorMessage}</p>}

      {/* Guidelines Header */}
      <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-slate-900">{brand.name} Guidelines</h2>
            <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-blue-50 text-blue-700 border border-blue-200">
              Active Brand Book
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1 max-w-xl">{brand.tagline}</p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleDeleteBrand}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 text-xs font-semibold"
          >
            <Trash2 className="w-3.5 h-3.5" />
            Delete Brand
          </button>
          <button
            onClick={() => loadBrandDetails()}
            className="p-2 rounded-lg bg-slate-50 hover:bg-slate-100 text-slate-600 border border-slate-200 transition-colors"
            title="Refresh Guidelines"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="flex items-center gap-1.5 border-b border-slate-200 pb-2 overflow-x-auto text-xs font-medium">
        {[
          { id: 'facts', label: `Approved Facts (${facts.length})`, icon: BookOpen },
          { id: 'terminology', label: `Vocabulary & Terms (${terminology.length})`, icon: AlertOctagon },
          { id: 'visuals', label: 'Color & Logo Rules', icon: Palette },
          { id: 'claims', label: `Claims & Legal (${claim_rules.length})`, icon: Scale },
          { id: 'brand_info', label: 'Tone Profile', icon: SlidersHorizontal }
        ].map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg transition-colors whitespace-nowrap ${
                isActive
                  ? 'bg-blue-600 text-white font-semibold shadow-xs'
                  : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* TAB 1: Approved Facts */}
      {activeTab === 'facts' && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-xs">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                Verified Single-Source-of-Truth Facts
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                The AI and rule-based governance engine verifies all marketing statements against these records.
              </p>
            </div>
            <button
              onClick={() => setModalType('fact')}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 shadow-xs transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add Fact</span>
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
            {facts.map((fact) => (
              <div
                key={fact.id}
                className="p-4 rounded-xl bg-slate-50 border border-slate-200 flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-blue-50 text-blue-700 border border-blue-200">
                      {fact.category}
                    </span>
                    {fact.key_metric && (
                      <span className="font-mono text-xs font-semibold text-emerald-800 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
                        {fact.key_metric}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-800 font-medium leading-relaxed">
                    "{fact.statement}"
                  </p>
                </div>

                <div className="mt-3 pt-2 border-t border-slate-200 flex items-center justify-between text-[11px] text-slate-400">
                  <span className="truncate max-w-[200px]">Triggers: {fact.keywords || 'General'}</span>
                  <button
                    onClick={() => handleDeleteFact(fact.id)}
                    className="text-slate-400 hover:text-rose-600 transition-colors p-1"
                    title="Delete Fact"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 2: Terminology Rules */}
      {activeTab === 'terminology' && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-xs">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                Prohibited Terminology & Alternatives
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                Banned words, off-brand slang, and buzzwords mapped directly to preferred vocabulary.
              </p>
            </div>
            <button
              onClick={() => setModalType('terminology')}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 shadow-xs transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add Term</span>
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase tracking-wider text-[10px]">
                  <th className="py-2.5 px-3 font-semibold">Severity</th>
                  <th className="py-2.5 px-3 font-semibold">Forbidden Term</th>
                  <th className="py-2.5 px-3 font-semibold">Approved Alternative</th>
                  <th className="py-2.5 px-3 font-semibold">Rationale</th>
                  <th className="py-2.5 px-3 font-semibold">Category</th>
                  <th className="py-2.5 px-3 text-right font-semibold">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {terminology.map((t) => (
                  <tr key={t.id} className="hover:bg-slate-50 transition-colors">
                    <td className="py-2.5 px-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                        t.severity === 'critical'
                          ? 'bg-rose-50 text-rose-700 border border-rose-200'
                          : t.severity === 'warning'
                          ? 'bg-amber-50 text-amber-700 border border-amber-200'
                          : 'bg-blue-50 text-blue-700 border border-blue-200'
                      }`}>
                        {t.severity}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-mono font-bold text-rose-700">
                      "{t.term}"
                    </td>
                    <td className="py-2.5 px-3 font-mono font-bold text-emerald-800">
                      "{t.replacement}"
                    </td>
                    <td className="py-2.5 px-3 text-slate-600 max-w-xs">
                      {t.reason}
                    </td>
                    <td className="py-2.5 px-3 text-slate-500">
                      {t.category}
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <button
                        onClick={() => handleDeleteTerminology(t.id)}
                        className="text-slate-400 hover:text-rose-600 transition-colors p-1"
                        title="Delete Rule"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* TAB 3: Visual & Color Rules */}
      {activeTab === 'visuals' && visual_rules && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-6 shadow-xs">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                Color Palette & Visual Rules
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                Brand primary hex codes, forbidden competitor colors, and clear space rules.
              </p>
            </div>
            <button
              onClick={handleSaveVisualRules}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 shadow-xs transition-colors"
            >
              <Save className="w-3.5 h-3.5" />
              <span>Save Rules</span>
            </button>
          </div>

          {/* Primary Colors */}
          <div>
            <h4 className="text-xs font-semibold text-slate-700 mb-2">Primary Palette</h4>
            <textarea
              rows={3}
              value={primaryColorsText}
              onChange={(event) => setPrimaryColorsText(event.target.value)}
              className="w-full mb-3 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
              placeholder={'One per line: Name: #hex'}
              aria-label="Primary brand colors"
            />
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {(visual_rules.primary_colors || []).map((c, i) => (
                <div key={i} className="p-3 rounded-lg bg-slate-50 border border-slate-200 flex items-center gap-3">
                  <div className="w-8 h-8 rounded border border-slate-300 shrink-0 shadow-inner" style={{ backgroundColor: c.hex }}></div>
                  <div className="overflow-hidden">
                    <span className="font-semibold text-xs text-slate-900 block truncate">{c.name}</span>
                    <span className="font-mono text-[11px] text-slate-500 block">{c.hex}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Secondary Colors */}
          <div>
            <h4 className="text-xs font-semibold text-slate-700 mb-2">Secondary Accents</h4>
            <textarea
              rows={3}
              value={secondaryColorsText}
              onChange={(event) => setSecondaryColorsText(event.target.value)}
              className="w-full mb-3 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
              placeholder={'One per line: Name: #hex'}
              aria-label="Secondary brand colors"
            />
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {(visual_rules.secondary_colors || []).map((c, i) => (
                <div key={i} className="p-3 rounded-lg bg-slate-50 border border-slate-200 flex items-center gap-3">
                  <div className="w-8 h-8 rounded border border-slate-300 shrink-0 shadow-inner" style={{ backgroundColor: c.hex }}></div>
                  <div className="overflow-hidden">
                    <span className="font-semibold text-xs text-slate-900 block truncate">{c.name}</span>
                    <span className="font-mono text-[11px] text-slate-500 block">{c.hex}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Forbidden Colors */}
          <div>
            <h4 className="text-xs font-semibold text-rose-700 mb-2">Forbidden / Competitor Colors</h4>
            <textarea
              rows={3}
              value={forbiddenColorsText}
              onChange={(event) => setForbiddenColorsText(event.target.value)}
              className="w-full mb-3 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs font-mono text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
              placeholder={'One per line: Name: #hex | reason'}
              aria-label="Forbidden brand colors"
            />
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {(visual_rules.forbidden_colors || []).map((c, i) => (
                <div key={i} className="p-3 rounded-lg bg-rose-50 border border-rose-200 flex items-center gap-3">
                  <div className="w-8 h-8 rounded border border-rose-300 shrink-0" style={{ backgroundColor: c.hex }}></div>
                  <div className="overflow-hidden">
                    <span className="font-semibold text-xs text-rose-800 block truncate">{c.name} ({c.hex})</span>
                    <span className="text-[11px] text-slate-500 block truncate">{c.reason}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Clear space & font settings */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Logo Placement & Clear Space</label>
              <textarea
                rows={3}
                value={logoRules}
                onChange={(e) => setLogoRules(e.target.value)}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
              />
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Typography Font Family</label>
                <input
                  type="text"
                  value={fontFamily}
                  onChange={(e) => setFontFamily(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-700 mb-1">Minimum Contrast Standard</label>
                <input
                  type="text"
                  value={contrastMin}
                  onChange={(e) => setContrastMin(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
                />
              </div>
            </div>
          </div>
          <p className="text-[11px] text-slate-500">
            Uploaded images can be reviewed for obvious color conflicts and required logo-like mark presence. Exact colors, logo identity and spacing, fonts, contrast, and layout still require manual review.
          </p>
        </div>
      )}

      {/* TAB 4: Claim & Compliance Rules */}
      {activeTab === 'claims' && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-xs">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                Compliance & Claim Verification Rules
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                Prohibits unsubstantiated absolute warranties and enforces mandatory disclaimers.
              </p>
            </div>
            <button
              onClick={() => setModalType('claim')}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 shadow-xs transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add Claim Rule</span>
            </button>
          </div>

          <div className="space-y-3 pt-2">
            {claim_rules.map((rule) => (
              <div
                key={rule.id}
                className="p-4 rounded-xl bg-slate-50 border border-slate-200 flex items-start justify-between gap-4"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-xs text-slate-900">{rule.rule_name}</span>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                      rule.severity === 'critical' ? 'bg-rose-50 text-rose-700 border border-rose-200' : 'bg-amber-50 text-amber-700 border border-amber-200'
                    }`}>
                      {rule.severity}
                    </span>
                  </div>
                  <div className="font-mono text-xs text-blue-700 bg-blue-50 px-2 py-0.5 rounded border border-blue-100 inline-block">
                    Pattern: {rule.pattern_or_trigger}
                  </div>
                  <p className="text-xs text-slate-600">{rule.explanation}</p>
                  {rule.replacement_text && (
                    <div className="text-[11px] text-emerald-800 font-medium">
                      Approved Replacement: "{rule.replacement_text}"
                    </div>
                  )}
                  {rule.required_disclaimer && (
                    <div className="text-[11px] text-emerald-800 font-medium">
                      Required Disclaimer: "{rule.required_disclaimer}"
                    </div>
                  )}
                </div>

                <button
                  onClick={() => handleDeleteClaim(rule.id)}
                  className="text-slate-400 hover:text-rose-600 transition-colors p-1"
                  title="Delete Claim Rule"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TAB 5: Tone Profile */}
      {activeTab === 'brand_info' && (
        <form onSubmit={handleSaveBrandInfo} className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-xs">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                Voice & Tone Profile
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                Core identity attributes used to evaluate formality and linguistic style.
              </p>
            </div>
            <button
              type="submit"
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 shadow-xs transition-colors"
            >
              <Save className="w-3.5 h-3.5" />
              <span>Save Profile</span>
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Brand Name</label>
              <input
                type="text"
                required
                value={editBrand.name}
                onChange={(e) => setEditBrand({ ...editBrand, name: e.target.value })}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Industry Sector</label>
              <input
                type="text"
                value={editBrand.industry}
                onChange={(e) => setEditBrand({ ...editBrand, industry: e.target.value })}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Tagline</label>
            <input
              type="text"
              value={editBrand.tagline}
              onChange={(e) => setEditBrand({ ...editBrand, tagline: e.target.value })}
              className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Description</label>
            <textarea
              rows={3}
              value={editBrand.description}
              onChange={(e) => setEditBrand({ ...editBrand, description: e.target.value })}
              className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Primary Tone & Voice</label>
              <input
                type="text"
                value={editBrand.primary_tone}
                onChange={(e) => setEditBrand({ ...editBrand, primary_tone: e.target.value })}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
                placeholder="e.g. Authoritative, Clear, Empathetic"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Voice Traits</label>
              <input
                type="text"
                value={(editBrand.voice_traits || []).join(', ')}
                onChange={(e) => setEditBrand({
                  ...editBrand,
                  voice_traits: e.target.value.split(',').map((trait) => trait.trim()).filter(Boolean)
                })}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
                placeholder="Comma-separated traits"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 mb-1">Target Audience</label>
              <input
                type="text"
                value={editBrand.target_audience}
                onChange={(e) => setEditBrand({ ...editBrand, target_audience: e.target.value })}
                className="w-full px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-900 focus:outline-none focus:border-blue-600 focus:bg-white"
              />
            </div>
          </div>
        </form>
      )}

      {/* Guideline Add Modal */}
      <GuidelineModal
        isOpen={Boolean(modalType)}
        onClose={() => setModalType(null)}
        type={modalType}
        brandId={brandId}
        onSubmit={handleModalSubmit}
      />
    </div>
  );
}
