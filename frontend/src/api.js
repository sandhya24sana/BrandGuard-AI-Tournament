
const API_BASE = import.meta.env?.DEV
  ? '/api'
  : 'https://brandguard-ai-h6rw.onrender.com/api';

/**Implement the smallest safe fix for the Apply frontend/backend version mismatch.

 * Robust fetch utility that verifies response status and content-type
 * before attempting JSON parsing.
 */
async function safeFetch(url, options = {}) {
  try {
    const res = await fetch(url, options);
    const contentType = res.headers.get('content-type') || '';

    let data = null;

    if (contentType.includes('application/json')) {
      data = await res.json();
    } else {
      const rawText = await res.text();
      const preview = rawText
        .replace(/<[^>]+>/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 150);

      return {
        success: false,
        error: res.ok
          ? `Unexpected non-JSON response from server: ${preview}`
          : `HTTP ${res.status} (${res.statusText || 'Error'}): ${preview || 'Request failed'}`
      };
    }

    if (!res.ok) {
      return {
        success: false,
        error: data?.error || `Server returned error status HTTP ${res.status}`,
        status: res.status,
        ...(data?.current_version !== undefined
          ? { current_version: data.current_version }
          : {})
      };
    }

    return data;
  } catch (err) {
    return {
      success: false,
      error: err.message || 'Network communication error'
    };
  }
}

export const api = {
  // System
  getHealth: async () => {
    return safeFetch(`${API_BASE}/health`);
  },

  getSystemStatus: async () => {
    return safeFetch(`${API_BASE}/analysis/system-status`);
  },

  // Brands
  getBrands: async () => {
    return safeFetch(`${API_BASE}/brands`);
  },

  getBrandDetails: async (brandId) => {
    if (!brandId) return { success: false, error: 'Brand ID is required' };
    return safeFetch(`${API_BASE}/brands/${brandId}`);
  },

  createBrand: async (data) => {
    return safeFetch(`${API_BASE}/brands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  deleteBrand: async (brandId) => {
    if (!brandId) return { success: false, error: 'Brand ID is required' };
    return safeFetch(`${API_BASE}/brands/${brandId}`, { method: 'DELETE' });
  },

  updateBrand: async (brandId, data) => {
    if (!brandId) return { success: false, error: 'Brand ID is required' };
    return safeFetch(`${API_BASE}/brands/${brandId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  // Guidelines Management
  addFact: async (brandId, data) => {
    return safeFetch(`${API_BASE}/brands/${brandId}/facts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  deleteFact: async (factId, brandId) => {
    if (!factId || !brandId) {
      return { success: false, error: 'Fact ID and brand ID are required' };
    }
    return safeFetch(
      `${API_BASE}/brands/facts/${factId}?brand_id=${encodeURIComponent(brandId)}`,
      { method: 'DELETE' }
    );
  },

  addTerminology: async (brandId, data) => {
    return safeFetch(`${API_BASE}/brands/${brandId}/terminology`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  deleteTerminology: async (termId, brandId) => {
    if (!termId || !brandId) {
      return { success: false, error: 'Terminology ID and brand ID are required' };
    }
    return safeFetch(
      `${API_BASE}/brands/terminology/${termId}?brand_id=${encodeURIComponent(brandId)}`,
      { method: 'DELETE' }
    );
  },

  updateVisualRules: async (brandId, data) => {
    return safeFetch(`${API_BASE}/brands/${brandId}/visual-rules`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  addClaimRule: async (brandId, data) => {
    return safeFetch(`${API_BASE}/brands/${brandId}/claims`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  deleteClaimRule: async (claimId, brandId) => {
    if (!claimId || !brandId) {
      return { success: false, error: 'Claim ID and brand ID are required' };
    }
    return safeFetch(
      `${API_BASE}/brands/claims/${claimId}?brand_id=${encodeURIComponent(brandId)}`,
      { method: 'DELETE' }
    );
  },

  // Content
  uploadFile: async (file) => {
    const formData = new FormData();
    formData.append('file', file);

    return safeFetch(`${API_BASE}/content/upload`, {
      method: 'POST',
      body: formData
    });
  },

  createContent: async (data) => {
    return safeFetch(`${API_BASE}/content`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  getContentList: async (brandId, status) => {
    let url = `${API_BASE}/content?`;

    if (brandId) url += `brand_id=${brandId}&`;
    if (status && status !== 'ALL') url += `status=${status}&`;

    return safeFetch(url);
  },

  getContentDetail: async (contentId, brandId) => {
    if (!contentId) return { success: false, error: 'Content ID is required' };
    const url = brandId
      ? `${API_BASE}/content/${contentId}?brand_id=${encodeURIComponent(brandId)}`
      : `${API_BASE}/content/${contentId}`;
    return safeFetch(url);
  },

  // Analysis
  quickCheck: async (data) => {
    return safeFetch(`${API_BASE}/analysis/quick-check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  analyzeContent: async (data) => {
    return safeFetch(`${API_BASE}/analysis/audit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  rewriteContent: async (data) => {
    return safeFetch(`${API_BASE}/analysis/rewrite`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  analyzeVisualFile: async (file, brandId) => {
    if (!file || !brandId) {
      return { success: false, error: 'An image and brand ID are required' };
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('brand_id', String(brandId));
    return safeFetch(`${API_BASE}/analysis/visual-check`, {
      method: 'POST',
      body: formData,
    });
  },

  runAnalysis: async (contentId, brandId) => {
    if (!contentId) {
      return {
        success: false,
        error: 'Content ID is required to run analysis on saved item.'
      };
    }
    const url = brandId
      ? `${API_BASE}/analysis/run/${contentId}?brand_id=${encodeURIComponent(brandId)}`
      : `${API_BASE}/analysis/run/${contentId}`;
    return safeFetch(url, {
      method: 'POST'
    });
  },

  applyCorrections: async (contentId, correctionRequest) => {
    if (!contentId) {
      return {
        success: false,
        error: 'Content ID is required to apply corrections.'
      };
    }

    const body = typeof correctionRequest === 'string'
      ? { corrected_text: correctionRequest }
      : correctionRequest;

    return safeFetch(`${API_BASE}/analysis/apply-corrections/${contentId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  },

  // Reviews
  submitReview: async (data) => {
    return safeFetch(`${API_BASE}/reviews`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  getRecentReviews: async () => {
    return safeFetch(`${API_BASE}/reviews/recent`);
  },

  // History & Analytics
  getDashboardStats: async (brandId) => {
    const url = brandId
      ? `${API_BASE}/history/dashboard-stats?brand_id=${brandId}`
      : `${API_BASE}/history/dashboard-stats`;

    return safeFetch(url);
  },

  exportAuditReport: async (contentId, brandId) => {
    if (!contentId) {
      return {
        success: false,
        error: 'Content ID is required to export report'
      };
    }
    const url = brandId
      ? `${API_BASE}/history/export/${contentId}?brand_id=${encodeURIComponent(brandId)}`
      : `${API_BASE}/history/export/${contentId}`;
    return safeFetch(url);
  }
};
