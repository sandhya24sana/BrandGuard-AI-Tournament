import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { Window } from 'happy-dom';
import { createServer } from 'vite';

const projectRoot = fileURLToPath(new URL('..', import.meta.url));

function response(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function deferredFetch() {
  const requests = [];
  globalThis.fetch = (url) => new Promise((resolve) => {
    requests.push({ url: String(url), resolve });
  });
  return requests;
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function createComponentHarness(context, componentPath) {
  const names = [
    'window', 'document', 'navigator', 'HTMLElement', 'Node', 'Event',
    'MouseEvent', 'MutationObserver', 'IS_REACT_ACT_ENVIRONMENT', 'fetch',
  ];
  const originalDescriptors = new Map(
    names.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)])
  );
  const window = new Window({ url: 'http://localhost/' });
  for (const [name, value] of Object.entries({
    window,
    document: window.document,
    navigator: window.navigator,
    HTMLElement: window.HTMLElement,
    Node: window.Node,
    Event: window.Event,
    MouseEvent: window.MouseEvent,
    MutationObserver: window.MutationObserver,
    IS_REACT_ACT_ENVIRONMENT: true,
  })) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value,
    });
  }

  const React = await import('react');
  const { act } = React;
  const { createRoot } = await import('react-dom/client');
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    root: projectRoot,
    logLevel: 'silent',
    server: { middlewareMode: true },
  });
  const { default: Component } = await vite.ssrLoadModule(componentPath);
  const container = window.document.createElement('div');
  window.document.body.appendChild(container);
  const root = createRoot(container);

  context.after(async () => {
    await act(async () => root.unmount());
    await vite.close();
    window.happyDOM.abort();
    for (const [name, descriptor] of originalDescriptors) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });

  return { React, act, Component, container, root, window };
}

function brandDetails(id, name) {
  return {
    success: true,
    brand: {
      id,
      name,
      tagline: `${name} tagline`,
      primary_tone: 'Clear',
      voice_traits: [],
    },
    facts: [],
    terminology: [],
    visual_rules: {
      primary_colors: [],
      secondary_colors: [],
      forbidden_colors: [],
    },
    claim_rules: [],
  };
}

function contentItem(id, title) {
  return {
    id,
    title,
    content_type: 'Ad Copy',
    channel: 'Digital',
    risk_score: 80,
    status: 'pending_review',
    current_version: 1,
    updated_at: '2026-01-01T00:00:00Z',
  };
}

test('GuidelinesManager ignores late responses from a previously selected brand', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/GuidelinesManager.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 1 }));
    await flush();
  });
  await act(async () => {
    root.render(React.createElement(Component, { brandId: 2 }));
    await flush();
  });
  assert.equal(requests.length, 2);

  await act(async () => {
    requests[1].resolve(response(brandDetails(2, 'Brand Two')));
    await flush();
  });
  assert.match(container.textContent, /Brand Two Guidelines/);

  await act(async () => {
    requests[0].resolve(response(brandDetails(1, 'Brand One')));
    await flush();
  });
  assert.match(container.textContent, /Brand Two Guidelines/);
  assert.doesNotMatch(container.textContent, /Brand One/);
});

test('GuidelinesManager exposes load failures and allows retry', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/GuidelinesManager.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 1 }));
    await flush();
  });
  await act(async () => {
    requests[0].resolve(response({ success: false, error: 'Guidelines unavailable' }, 503));
    await flush();
  });
  assert.match(container.textContent, /Guidelines unavailable/);

  await act(async () => {
    container.querySelector('button').dispatchEvent(
      new window.MouseEvent('click', { bubbles: true }),
    );
    await flush();
  });
  await act(async () => {
    requests[1].resolve(response(brandDetails(1, 'Recovered Brand')));
    await flush();
  });
  assert.match(container.textContent, /Recovered Brand Guidelines/);
});

test('ReviewHistory clears prior-brand content and ignores late list responses', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/ReviewHistory.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 1 }));
    await flush();
  });
  await act(async () => {
    root.render(React.createElement(Component, { brandId: 2 }));
    await flush();
  });
  assert.equal(requests.length, 2);

  await act(async () => {
    requests[1].resolve(response({ success: true, items: [contentItem(2, 'Brand Two Content')] }));
    await flush();
  });
  assert.match(container.textContent, /Brand Two Content/);

  await act(async () => {
    requests[0].resolve(response({ success: true, items: [contentItem(1, 'Brand One Content')] }));
    await flush();
  });
  assert.match(container.textContent, /Brand Two Content/);
  assert.doesNotMatch(container.textContent, /Brand One Content/);
});

test('ReviewHistory shows API failures instead of stale or empty results', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/ReviewHistory.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 1 }));
    await flush();
  });
  await act(async () => {
    requests[0].resolve(response({ success: false, error: 'History unavailable' }, 503));
    await flush();
  });
  assert.match(container.textContent, /History unavailable/);
  assert.equal(container.querySelector('[role="alert"]')?.textContent, 'History unavailable');
});

test('ReviewHistory compares any two saved versions with their numbers and timestamps', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/ReviewHistory.jsx',
  );
  const versions = [
    { id: 33, version_number: 3, created_at: '2026-03-03T12:00:00Z', text: 'Third version copy' },
    { id: 32, version_number: 2, created_at: '2026-03-02T12:00:00Z', text: 'Second version copy' },
    { id: 31, version_number: 1, created_at: '2026-03-01T12:00:00Z', text: 'First version copy' },
  ];
  const requests = [];
  globalThis.fetch = async (url) => {
    const requestUrl = String(url);
    requests.push(requestUrl);
    if (requestUrl.includes('/api/content?')) {
      return response({ success: true, items: [contentItem(44, 'Versioned content')] });
    }
    if (requestUrl.includes('/api/content/44?')) {
      return response({
        success: true,
        content: {
          id: 44,
          brand_id: 7,
          title: 'Versioned content',
          current_version: 3,
          status: 'pending_review',
          risk_score: 80,
          original_text: versions[0].text,
          corrected_text: versions[0].text,
        },
        versions,
        reviews: [],
      });
    }
    return response({ success: false, error: 'Unexpected request' }, 404);
  };

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 7 }));
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Audit Log'))
      .click();
    await flush();
  });
  assert.ok(requests.some((url) => url.includes('/api/content/44?brand_id=7')));

  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Compare Versions'))
      .click();
    await flush();
  });

  const leftSelect = container.querySelector('select[aria-label="First saved version"]');
  const setSelectValue = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    'value',
  ).set;
  await act(async () => {
    setSelectValue.call(leftSelect, '3');
    leftSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
    await flush();
  });
  assert.equal(
    container.querySelector('[role="alert"]')?.textContent,
    'Select two different saved versions to compare.',
  );

  await act(async () => {
    setSelectValue.call(leftSelect, '1');
    leftSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
    await flush();
  });
  assert.match(container.textContent, /Version 1 -/);
  assert.match(container.textContent, /Version 3 -/);
  assert.ok(container.textContent.includes(
    new Date(versions[0].created_at).toLocaleString()
  ));
  assert.match(container.textContent, /First version copy/);
  assert.match(container.textContent, /Third version copy/);
  assert.doesNotMatch(container.textContent, /Select two different saved versions/);
});

test('ReviewHistory handles missing and identical saved versions', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/ReviewHistory.jsx',
  );
  const versionSets = {
    45: [{ id: 1, version_number: 1, created_at: '2026-03-01T12:00:00Z', text: 'Only saved version' }],
    46: [
      { id: 2, version_number: 2, created_at: '2026-03-02T12:00:00Z', text: 'Same saved text' },
      { id: 3, version_number: 1, created_at: '2026-03-01T12:00:00Z', text: 'Same saved text' },
    ],
  };
  globalThis.fetch = async (url) => {
    const requestUrl = String(url);
    if (requestUrl.includes('/api/content?')) {
      return response({
        success: true,
        items: [
          contentItem(45, 'One-version content'),
          contentItem(46, 'Identical-version content'),
        ],
      });
    }
    const contentId = Number(new URL(requestUrl, 'http://localhost').pathname.split('/').pop());
    return response({
      success: true,
      content: {
        id: contentId,
        brand_id: 7,
        title: 'Versioned content',
        current_version: 2,
        status: 'pending_review',
        risk_score: 80,
        original_text: '',
        corrected_text: '',
      },
      versions: versionSets[contentId],
      reviews: [],
    });
  };

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 7 }));
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Audit Log'))
      .click();
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Compare Versions'))
      .click();
    await flush();
  });
  assert.match(container.textContent, /At least two saved versions are required for comparison/);

  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Close'))
      .click();
    await flush();
  });
  const identicalItemRow = Array.from(container.querySelectorAll('tbody tr'))
    .find((row) => row.textContent.includes('Identical-version content'));
  await act(async () => {
    Array.from(identicalItemRow.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Audit Log'))
      .click();
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Compare Versions'))
      .click();
    await flush();
  });
  assert.match(container.textContent, /These versions contain identical text/);
  assert.match(container.textContent, /Same saved text/);
});

test('ContentAnalyzer records a review against the version created during submission', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/ContentAnalyzer.jsx',
  );
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const request = {
      url: String(url),
      body: options.body ? JSON.parse(options.body) : null,
    };
    calls.push(request);
    if (request.url.endsWith('/api/analysis/audit')) {
      return response({
        success: true,
        content_id: 31,
        version: 4,
        status: 'pending_review',
        result: {
          score: 100,
          risk_level: 'LOW',
          sub_scores: {
            terminology: 100,
            facts: 100,
            tone: 100,
            claims: 100,
            accessibility: 100,
          },
          issues: [],
          summary: 'No configured issues.',
          corrected_text: request.body.text,
          engine_used: 'RULE_BASED_FALLBACK',
          visual_validation: { supported: false, message: 'Not validated.' },
          readability: {
            reading_ease: 80,
            grade_level: 5,
            sentence_count: 1,
            reading_time_sec: 1,
          },
        },
      });
    }
    if (request.url.endsWith('/api/reviews')) {
      return response({
        success: true,
        new_status: 'approved',
        message: 'Review recorded.',
      });
    }
    return response({ success: false, error: 'Unexpected request' }, 404);
  };

  await act(async () => {
    root.render(React.createElement(Component, {
      brandId: 7,
      brandName: 'Review Test Brand',
    }));
    await flush();
  });
  const editor = container.querySelector('textarea');
  const valueSetter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value',
  ).set;
  await act(async () => {
    valueSetter.call(editor, ' Draft content ');
    editor.dispatchEvent(new window.Event('input', { bubbles: true }));
    await flush();
  });
  await act(async () => {
    container.querySelector('[title="Sign-off & Approve Content"]').click();
    await flush();
  });

  const reviewerName = container.querySelector('input[placeholder="Reviewer Name"]');
  const nameSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value',
  ).set;
  await act(async () => {
    nameSetter.call(reviewerName, 'Reviewer');
    reviewerName.dispatchEvent(new window.Event('input', { bubbles: true }));
    await flush();
  });
  await act(async () => {
    container.querySelector('button[type="submit"]').click();
    await flush();
  });

  const auditCall = calls.find((call) => call.url.endsWith('/api/analysis/audit'));
  const reviewCall = calls.find((call) => call.url.endsWith('/api/reviews'));
  assert.equal(auditCall.body.text, ' Draft content ');
  assert.equal(reviewCall.body.content_id, 31);
  assert.equal(reviewCall.body.content_version, 4);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3600));
  });
});

test('ContentAnalyzer preserves original copy and shows only the saved revision status', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/ContentAnalyzer.jsx',
  );
  const sourceText = 'Original restricted copy';
  const appliedText = 'Corrected once';
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    const request = {
      url: String(url),
      body: options.body ? JSON.parse(options.body) : null,
    };
    requests.push(request);
    if (request.url.includes('/api/content/33?')) {
      return response({
        success: true,
        content: { id: 33, brand_id: 7, current_version: 1 },
      });
    }
    if (request.url.endsWith('/api/analysis/audit')) {
      return response({
        success: true,
        content_id: 33,
        version: 1,
        status: 'pending_review',
        result: {
          score: 40,
          risk_level: 'CRITICAL',
          sub_scores: {
            terminology: 40,
            facts: 100,
            tone: 100,
            claims: 100,
            accessibility: 100,
          },
          issues: [{
            id: 'term_1_0_8',
            category: 'Terminology',
            severity: 'CRITICAL',
            rule: 'Prohibited Term',
            matched_text: 'restricted',
            replacement_text: 'Corrected once',
            auto_fixable: true,
            location: { start: 8, end: 18 },
          }],
          summary: 'A configured violation remains under review.',
          corrected_text: appliedText,
          revision_compliance: {
            approved: false,
            remaining_issue_count: 1,
          },
          engine_used: 'RULE_BASED_FALLBACK',
          visual_validation: { supported: false, message: 'Not validated.' },
          readability: {
            reading_ease: 80,
            grade_level: 5,
            sentence_count: 1,
            reading_time_sec: 1,
          },
        },
      });
    }
    if (request.url.endsWith('/api/analysis/apply-corrections/33')) {
      return response({
        success: true,
        new_version: 2,
        applied_text: appliedText,
        correction_review: {
          remaining_issue_count: 0,
          remaining_critical_issue_count: 1,
          compliant: false,
        },
        result: {
          score: 40,
          risk_level: 'CRITICAL',
          issues: [{
            id: 'remaining',
            category: 'Terminology',
            severity: 'CRITICAL',
            matched_text: 'Corrected',
          }],
          corrected_text: 'Corrected twice',
        },
      });
    }
    return response({ success: false, error: 'Unexpected request' }, 404);
  };

  await act(async () => {
    root.render(React.createElement(Component, {
      brandId: 7,
      brandName: 'Revision Test Brand',
    }));
    await flush();
  });
  const editor = container.querySelector('textarea');
  const valueSetter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value',
  ).set;
  await act(async () => {
    valueSetter.call(editor, sourceText);
    editor.dispatchEvent(new window.Event('input', { bubbles: true }));
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Audit Content'))
      .click();
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Diff & Changes'))
      .click();
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Adopt All Corrections'))
      .click();
    await flush();
  });

  const textBlocks = Array.from(container.querySelectorAll('.whitespace-pre-wrap'))
    .map((element) => element.textContent);
  assert.ok(textBlocks.includes(sourceText));
  assert.ok(textBlocks.includes(appliedText));
  assert.doesNotMatch(container.textContent, /Corrected twice/);
  assert.match(container.textContent, /Needs Review/);
  assert.doesNotMatch(container.textContent, /Compliance Approved/);
  assert.equal(requests.filter((request) =>
    request.url.endsWith('/api/analysis/audit')).length, 1);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3600));
  });
});

test('ContentAnalyzer shows per-issue actions and hides them after a stale-version check', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/ContentAnalyzer.jsx',
  );
  const sourceText = 'alpha beta omega tail';
  const calls = [];
  let contentDetailReads = 0;
  const issues = [
    {
      id: 'first',
      category: 'Terminology',
      severity: 'WARNING',
      rule: 'First actionable finding',
      matched_text: 'alpha',
      suggestion: 'first',
      replacement_text: 'first',
      auto_fixable: true,
      location: { start: 0, end: 5 },
    },
    {
      id: 'manual',
      category: 'Fact Accuracy',
      severity: 'WARNING',
      rule: 'Manual-review finding',
      matched_text: 'beta',
      suggestion: 'Verify the supporting evidence.',
      replacement_text: null,
      auto_fixable: false,
      manual_review_required: true,
      location: { start: 6, end: 10 },
    },
    {
      id: 'middle',
      category: 'Voice & Tone',
      severity: 'WARNING',
      rule: 'Middle actionable finding',
      matched_text: 'omega',
      suggestion: 'middle',
      replacement_text: 'middle',
      auto_fixable: true,
      location: { start: 11, end: 16 },
    },
  ];
  const conflictOptions = [
    {
      issue_id: 'conflict-1',
      rule: 'Alternative one',
      replacement_text: 'affordable',
    },
    {
      issue_id: 'conflict-2',
      rule: 'Alternative two',
      replacement_text: 'budget-friendly',
    },
  ];
  issues.push(
    ...conflictOptions.map((option) => ({
      id: option.issue_id,
      category: 'Terminology',
      severity: 'WARNING',
      rule: option.rule,
      matched_text: 'beta',
      suggestion: option.replacement_text,
      replacement_text: option.replacement_text,
      auto_fixable: false,
      replacement_choice_required: true,
      manual_review_required: true,
      replacement_options: conflictOptions,
      location: { start: 6, end: 10 },
    })),
    {
      id: 'last',
      category: 'Compliance & Claims',
      severity: 'WARNING',
      rule: 'Last actionable finding',
      matched_text: 'tail',
      suggestion: 'final',
      replacement_text: 'final',
      auto_fixable: true,
      location: { start: 17, end: 21 },
    },
  );

  globalThis.fetch = async (url, options = {}) => {
    const request = {
      url: String(url),
      method: options.method || 'GET',
      body: options.body ? JSON.parse(options.body) : null,
    };
    calls.push(request);
    if (request.url.includes('/api/content/71?')) {
      contentDetailReads += 1;
      return response({
        success: true,
        content: {
          id: 71,
          brand_id: 7,
          current_version: contentDetailReads === 1 ? 4 : 6,
        },
      });
    }
    if (request.url.endsWith('/api/analysis/audit')) {
      return response({
        success: true,
        content_id: 71,
        version: 5,
        status: 'pending_review',
        result: {
          score: 60,
          risk_level: 'MODERATE',
          sub_scores: {
            terminology: 60,
            facts: 60,
            tone: 60,
            claims: 60,
            accessibility: 60,
          },
          issues,
          summary: 'Several findings require review.',
          corrected_text: sourceText,
          engine_used: 'AI_GEMINI',
          readability: {
            reading_ease: 70,
            grade_level: 6,
            sentence_count: 1,
            reading_time_sec: 1,
          },
        },
      });
    }
    return response({ success: false, error: 'Unexpected request' }, 404);
  };

  await act(async () => {
    root.render(React.createElement(Component, {
      brandId: 7,
      brandName: 'Apply Test Brand',
      contentToEdit: {
        id: 71,
        title: 'Apply test content',
        content_type: 'Ad Copy',
        channel: 'Website',
        original_text: sourceText,
        current_version: 4,
        status: 'pending_review',
      },
    }));
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Audit Content'))
      .click();
    await flush();
  });

  const applyButtons = () => Array.from(container.querySelectorAll('button'))
    .filter((button) => button.textContent.trim() === 'Apply');
  let visibleApplyButtons = applyButtons();
  assert.equal(visibleApplyButtons.length, 5);
  assert.equal(
    visibleApplyButtons.filter((button) => !button.disabled).length,
    3,
  );

  await act(async () => {
    visibleApplyButtons.find((button) => !button.disabled).click();
    await flush();
  });
  assert.equal(
    calls.some((call) => call.url.includes('/api/analysis/apply-corrections/')),
    false,
  );
  assert.equal(applyButtons().length, 0);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3600));
  });
});

test('ContentAnalyzer sends one selected issue and its audited version for individual Apply', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/ContentAnalyzer.jsx',
  );
  const sourceText = 'alpha beta';
  const calls = [];
  let contentDetailReads = 0;
  const issues = [
    {
      id: 'first',
      category: 'Terminology',
      severity: 'WARNING',
      rule: 'First correction',
      matched_text: 'alpha',
      suggestion: 'one',
      replacement_text: 'one',
      auto_fixable: true,
      location: { start: 0, end: 5 },
    },
    {
      id: 'second',
      category: 'Voice & Tone',
      severity: 'WARNING',
      rule: 'Second correction',
      matched_text: 'beta',
      suggestion: 'two',
      replacement_text: 'two',
      auto_fixable: true,
      location: { start: 6, end: 10 },
    },
  ];

  globalThis.fetch = async (url, options = {}) => {
    const request = {
      url: String(url),
      method: options.method || 'GET',
      body: options.body ? JSON.parse(options.body) : null,
    };
    calls.push(request);
    if (request.url.includes('/api/content/71?')) {
      contentDetailReads += 1;
      return response({
        success: true,
        content: {
          id: 71,
          brand_id: 7,
          current_version: contentDetailReads === 1 ? 4 : 5,
        },
      });
    }
    if (request.url.endsWith('/api/analysis/audit')) {
      return response({
        success: true,
        content_id: 71,
        version: 5,
        status: 'pending_review',
        result: {
          score: 70,
          risk_level: 'LOW',
          sub_scores: {},
          issues,
          summary: 'Two findings require correction.',
          corrected_text: sourceText,
          engine_used: 'RULE_BASED_FALLBACK',
          readability: {
            reading_ease: 70,
            grade_level: 6,
            sentence_count: 1,
            reading_time_sec: 1,
          },
        },
      });
    }
    if (request.url.endsWith('/api/analysis/apply-corrections/71')) {
      return response({
        success: true,
        new_version: 6,
        applied_text: 'alpha two',
        applied_issue_ids: ['second'],
        correction_review: { compliant: false },
        result: {
          score: 80,
          risk_level: 'LOW',
          sub_scores: {},
          issues: [issues[0]],
          summary: 'One finding remains.',
          corrected_text: 'alpha two',
          engine_used: 'RULE_BASED_FALLBACK',
          readability: {
            reading_ease: 70,
            grade_level: 6,
            sentence_count: 1,
            reading_time_sec: 1,
          },
        },
      });
    }
    return response({ success: false, error: 'Unexpected request' }, 404);
  };

  await act(async () => {
    root.render(React.createElement(Component, {
      brandId: 7,
      brandName: 'Apply Test Brand',
      contentToEdit: {
        id: 71,
        title: 'Version-bound Apply',
        content_type: 'Ad Copy',
        channel: 'Website',
        original_text: sourceText,
        current_version: 4,
        status: 'pending_review',
      },
    }));
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Audit Content'))
      .click();
    await flush();
  });

  const auditRequest = calls.find((call) => call.url.endsWith('/api/analysis/audit'));
  assert.equal(auditRequest.body.current_version, 4);
  const applyButtons = Array.from(container.querySelectorAll('button'))
    .filter((button) => button.textContent.trim() === 'Apply');
  assert.equal(applyButtons.length, 2);

  await act(async () => {
    applyButtons[1].click();
    await flush();
  });

  const applyRequest = calls.find((call) =>
    call.url.endsWith('/api/analysis/apply-corrections/71'));
  assert.deepEqual(applyRequest.body, {
    brand_id: 7,
    issue_ids: ['second'],
    content_version: 5,
  });
  assert.equal(contentDetailReads, 2);
  assert.equal(container.textContent.includes('alpha two'), true);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3600));
  });
});

test('ContentAnalyzer previews an AI rewrite and saves it only after confirmation', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/ContentAnalyzer.jsx',
  );
  const originalText = 'Existing version copy';
  const rewrittenText = 'Gemini rewrite preview';
  const calls = [];
  let allowSave = false;
  let confirmationCount = 0;
  window.confirm = () => {
    confirmationCount += 1;
    return allowSave;
  };
  globalThis.fetch = async (url, options = {}) => {
    const request = {
      url: String(url),
      method: options.method || 'GET',
      body: options.body ? JSON.parse(options.body) : null,
    };
    calls.push(request);
    if (request.url.endsWith('/api/analysis/rewrite')) {
      return response({
        success: true,
        rewritten_text: rewrittenText,
        engine_used: 'AI_GEMINI',
        saved: false,
      });
    }
    if (request.url.includes('/api/content/71?')) {
      return response({
        success: true,
        content: { id: 71, brand_id: 7, current_version: 3 },
      });
    }
    if (request.url.endsWith('/api/analysis/audit')) {
      return response({
        success: true,
        content_id: 71,
        version: 4,
        status: 'pending_review',
        result: {
          score: 100,
          risk_level: 'LOW',
          sub_scores: {
            terminology: 100,
            facts: 100,
            tone: 100,
            claims: 100,
            accessibility: 100,
          },
          issues: [],
          summary: 'No configured issues.',
          corrected_text: rewrittenText,
          engine_used: 'RULE_BASED_FALLBACK',
          visual_validation: { supported: false, message: 'Not validated.' },
          readability: {
            reading_ease: 80,
            grade_level: 5,
            sentence_count: 1,
            reading_time_sec: 1,
          },
        },
      });
    }
    return response({ success: false, error: 'Unexpected request' }, 404);
  };

  await act(async () => {
    root.render(React.createElement(Component, {
      brandId: 7,
      brandName: 'Rewrite Test Brand',
      contentToEdit: {
        id: 71,
        title: 'Existing content',
        content_type: 'Ad Copy',
        channel: 'Website',
        original_text: originalText,
        current_version: 3,
        status: 'pending_review',
      },
    }));
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('AI Rewrite'))
      .click();
    await flush();
  });

  const rewriteCall = calls.find((call) => call.url.endsWith('/api/analysis/rewrite'));
  assert.deepEqual(rewriteCall.body, {
    brand_id: 7,
    text: originalText,
    content_type: 'Ad Copy',
    channel: 'Website',
  });
  assert.equal(container.querySelector('textarea').value, originalText);
  assert.match(container.textContent, /Gemini rewrite preview/);
  assert.match(container.textContent, /original content remains unchanged until you save/i);
  assert.equal(calls.some((call) => call.url.endsWith('/api/analysis/audit')), false);

  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Save as New Version'))
      .click();
    await flush();
  });
  assert.equal(confirmationCount, 1);
  assert.equal(calls.some((call) => call.url.endsWith('/api/analysis/audit')), false);
  assert.equal(container.querySelector('textarea').value, originalText);

  allowSave = true;
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Save as New Version'))
      .click();
    await flush();
  });

  const auditCall = calls.find((call) => call.url.endsWith('/api/analysis/audit'));
  assert.equal(confirmationCount, 2);
  assert.equal(auditCall.body.content_id, 71);
  assert.equal(auditCall.body.current_version, 3);
  assert.equal(auditCall.body.brand_id, 7);
  assert.equal(auditCall.body.text, rewrittenText);
  assert.equal(container.querySelector('textarea').value, rewrittenText);
  assert.match(container.textContent, /\(v4\)/);
  assert.doesNotMatch(container.textContent, /AI Rewrite Preview/);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3600));
  });
});

test('ContentAnalyzer shows AI rewrite loading and error states', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/ContentAnalyzer.jsx',
  );
  const requests = deferredFetch();

  await act(async () => {
    root.render(React.createElement(Component, {
      brandId: 7,
      brandName: 'Rewrite Test Brand',
    }));
    await flush();
  });
  const editor = container.querySelector('textarea');
  const valueSetter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    'value',
  ).set;
  await act(async () => {
    valueSetter.call(editor, 'Content to rewrite');
    editor.dispatchEvent(new window.Event('input', { bubbles: true }));
    await flush();
  });
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('AI Rewrite'))
      .click();
    await flush();
  });

  assert.match(container.textContent, /Generating AI rewrite\.\.\./);
  await act(async () => {
    requests[0].resolve(response({
      success: false,
      error: 'Gemini rewrite is unavailable.',
    }, 503));
    await flush();
  });
  assert.equal(container.querySelector('[role="alert"]')?.textContent, 'Gemini rewrite is unavailable.');
  assert.match(container.textContent, /AI Rewrite/);
});

test('App reports brand-load failures instead of showing an empty-brand state', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/App.jsx',
  );
  let brandRequestCount = 0;
  let completeRetry;
  globalThis.fetch = async (url) => {
    if (String(url).endsWith('/api/brands')) {
      brandRequestCount += 1;
      if (brandRequestCount > 1) {
        return new Promise((resolve) => {
          completeRetry = () => resolve(response({
            success: false,
            error: 'Brand service unavailable',
          }, 503));
        });
      }
      return response({ success: false, error: 'Brand service unavailable' }, 503);
    }
    return response({
      success: true,
      ai_configured: false,
      active_engine: 'Rule-Based Fallback',
      mode: 'Rule-Based Fallback',
    });
  };

  await act(async () => {
    root.render(React.createElement(Component));
    await flush();
    await flush();
  });
  assert.match(container.textContent, /Brand service unavailable/);
  assert.doesNotMatch(container.textContent, /Create your first brand/);
  assert.ok(
    Array.from(container.querySelectorAll('button')).some((button) => button.textContent.includes('Retry')),
  );
  await act(async () => {
    Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent.includes('Retry'))
      .click();
    await Promise.resolve();
  });
  assert.match(container.textContent, /Loading BrandGuard AI/);
  assert.doesNotMatch(container.textContent, /Create your first brand/);
  await act(async () => {
    completeRetry();
    await flush();
  });
  assert.match(container.textContent, /Brand service unavailable/);
});

test('ReviewHistory surfaces report-export API failures', async (context) => {
  const { React, act, Component, container, root } = await createComponentHarness(
    context,
    '/src/pages/ReviewHistory.jsx',
  );
  globalThis.fetch = async (url) => {
    if (String(url).includes('/api/content?')) {
      return response({ success: true, items: [contentItem(2, 'Export failure report')] });
    }
    return response({ success: false, error: 'Report service unavailable' }, 503);
  };

  await act(async () => {
    root.render(React.createElement(Component, { brandId: 2 }));
    await flush();
  });
  await act(async () => {
    container.querySelector('[title="Download JSON Report"]').click();
    await flush();
  });
  assert.match(container.textContent, /Report service unavailable/);
  assert.equal(container.querySelector('[role="alert"]')?.textContent, 'Report service unavailable');
});

test('ContentAnalyzer reviews image uploads separately from saved text content', async (context) => {
  const { React, act, Component, container, root, window } = await createComponentHarness(
    context,
    '/src/pages/ContentAnalyzer.jsx',
  );
  const originalFormData = globalThis.FormData;
  const formValues = new Map();
  globalThis.FormData = class TestFormData {
    append(name, value) {
      formValues.set(name, value);
    }

    get(name) {
      return formValues.get(name);
    }
  };
  context.after(() => {
    globalThis.FormData = originalFormData;
  });

  let request;
  globalThis.fetch = async (url, options = {}) => {
    request = { url: String(url), options };
    return response({
      success: true,
      filename: 'creative.png',
      saved: false,
      visual_validation: {
        supported: true,
        message: 'Preliminary review; manual verification required.',
        checks: [{
          id: 'forbidden_colors',
          rule: 'Obvious use of configured forbidden colors',
          status: 'concern',
          evidence: 'A prominent red background is visible.',
          confidence: 'medium',
          manual_review_required: true,
        }],
        not_evaluated: [{
          rule: 'Exact brand color values',
          reason: 'Pixel-level color matching is not verified.',
        }],
      },
    });
  };

  await act(async () => {
    root.render(React.createElement(Component, {
      brandId: 7,
      brandName: 'Visual Test Brand',
    }));
    await flush();
  });
  const input = container.querySelector('input[type="file"]');
  const image = new window.File(
    [new Uint8Array([137, 80, 78, 71])],
    'creative.png',
    { type: 'image/png' },
  );
  Object.defineProperty(input, 'files', {
    configurable: true,
    value: [image],
  });

  await act(async () => {
    input.dispatchEvent(new window.Event('change', { bubbles: true }));
    await flush();
  });

  assert.equal(request.url.endsWith('/api/analysis/visual-check'), true);
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.body.get('brand_id'), '7');
  assert.equal(request.options.body.get('file').name, 'creative.png');
  assert.equal(container.querySelector('textarea').value, '');
  assert.match(container.textContent, /Visual Brand Review/);
  assert.match(container.textContent, /A prominent red background is visible/);
  assert.match(container.textContent, /Exact brand color values/);
  assert.match(container.textContent, /manual review required/i);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 3600));
  });
});
