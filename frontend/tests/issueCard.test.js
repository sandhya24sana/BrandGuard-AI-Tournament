import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

function findButton(element) {
  if (Array.isArray(element)) {
    for (const child of element) {
      const button = findButton(child);
      if (button) return button;
    }
    return null;
  }
  if (!element || typeof element !== 'object') return null;
  if (element.type === 'button') return element;
  return findButton(element.props?.children);
}

function findSelect(element) {
  if (Array.isArray(element)) {
    for (const child of element) {
      const select = findSelect(child);
      if (select) return select;
    }
    return null;
  }
  if (!element || typeof element !== 'object') return null;
  if (element.type === 'select') return element;
  return findSelect(element.props?.children);
}

test('IssueCard shows Apply for a fixable legacy weight-loss claim', async () => {
  const projectRoot = fileURLToPath(new URL('..', import.meta.url));
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    root: projectRoot,
    logLevel: 'silent',
    server: { middlewareMode: true }
  });
  try {
    const { default: IssueCard } = await vite.ssrLoadModule('/src/components/IssueCard.jsx');
    const fixableIssue = {
      id: 'claim_1_10_32',
      category: 'Compliance & Claims',
      severity: 'CRITICAL',
      rule: 'Unsupported Medical Claims',
      matched_text: 'guarantees weight loss',
      replacement_text: 'Enjoy food made with natural ingredients.',
      suggestion: 'Enjoy food made with natural ingredients.',
      required_disclaimer: 'Avoid medical claims. Use: "Enjoy food made with natural ingredients."',
      auto_fixable: true,
      location: { start: 10, end: 32 }
    };
    let appliedIssue;
    const card = IssueCard({
      issue: fixableIssue,
      sourceText: '0123456789guarantees weight loss',
      onApplyFix: (issue) => { appliedIssue = issue; }
    });

    assert.match(renderToStaticMarkup(card), /<button\b[^>]*>.*Apply/s);
    findButton(card).props.onClick();
    assert.equal(appliedIssue, fixableIssue);

    const manualIssue = {
      ...fixableIssue,
      id: 'claim_1_10_23',
      replacement_text: 'Enjoy food made with natural ingredients.',
      suggestion: 'Enjoy food made with natural ingredients.',
      required_disclaimer: 'Use only substantiated claims.',
      auto_fixable: false
    };
    const manualCard = IssueCard({
      issue: manualIssue,
      sourceText: '0123456789guarantees weight loss',
      onApplyFix: () => {}
    });
    assert.doesNotMatch(renderToStaticMarkup(manualCard), /<button\b/);
    assert.match(renderToStaticMarkup(manualCard), /Required Disclaimer/);
  } finally {
    await vite.close();
  }
});

test('IssueCard lets users select a backend-validated replacement option', async () => {
  const projectRoot = fileURLToPath(new URL('..', import.meta.url));
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    root: projectRoot,
    logLevel: 'silent',
    server: { middlewareMode: true }
  });
  try {
    const { default: IssueCard } = await vite.ssrLoadModule('/src/components/IssueCard.jsx');
    const issue = {
      id: 'term-cheap',
      category: 'Terminology',
      severity: 'CRITICAL',
      rule: 'Prohibited Term: cheap',
      matched_text: 'cheap',
      replacement_text: 'affordable',
      auto_fixable: false,
      replacement_choice_required: true,
      replacement_options: [
        {
          issue_id: 'term-cheap',
          rule: 'Prohibited Term: cheap',
          replacement_text: 'affordable'
        },
        {
          issue_id: 'claim-cheap',
          rule: 'Affordable wording',
          replacement_text: 'budget-friendly'
        }
      ],
      location: { start: 0, end: 5 }
    };
    const conflictingIssues = [
      issue,
      {
        ...issue,
        id: 'claim-cheap',
        replacement_text: 'budget-friendly',
      },
    ];
    const selectedOptionIds = {};
    let applied;
    const renderCards = () => conflictingIssues.map((conflictingIssue) =>
      IssueCard({
        issue: conflictingIssue,
        sourceText: 'cheap plans',
        selectedReplacementOptionId: selectedOptionIds[conflictingIssue.id],
        onReplacementOptionChange: (issueId, optionId) => {
          selectedOptionIds[issueId] = optionId;
        },
        onApplyFix: (selectedIssue, optionId) => {
          applied = { cardIssueId: selectedIssue.id, optionId };
        }
      })
    );

    let cards = renderCards();
    assert.match(cards.map(renderToStaticMarkup).join(''), /budget-friendly/);
    assert.ok(cards.every((card) => findButton(card).props.disabled));
    findSelect(cards[1]).props.onChange({ target: { value: 'claim-cheap' } });

    cards = renderCards();
    assert.equal(findButton(cards[1]).props.disabled, false);
    findButton(cards[1]).props.onClick();
    assert.deepEqual(applied, {
      cardIssueId: 'claim-cheap',
      optionId: 'claim-cheap',
    });

    const staleCard = IssueCard({
      issue,
      sourceText: 'latest content no longer has the span',
      selectedReplacementOptionId: 'claim-cheap',
      onApplyFix: () => {}
    });
    assert.doesNotMatch(renderToStaticMarkup(staleCard), /<button\b/);

    const staleVersionCard = IssueCard({
      issue: conflictingIssues[1],
      sourceText: 'cheap plans',
      selectedReplacementOptionId: 'claim-cheap',
      onApplyFix: () => {},
      isAuditStale: true,
    });
    assert.doesNotMatch(renderToStaticMarkup(staleVersionCard), /<button\b/);
  } finally {
    await vite.close();
  }
});

test('IssueCard shows an individual Apply button for every valid correction', async () => {
  const projectRoot = fileURLToPath(new URL('..', import.meta.url));
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    root: projectRoot,
    logLevel: 'silent',
    server: { middlewareMode: true }
  });
  try {
    const { default: IssueCard } = await vite.ssrLoadModule('/src/components/IssueCard.jsx');
    const sourceText = 'The 🌿 lotion cures rash and prevents irritation.';
    const codePointSpan = (matchedText) => {
      const start = Array.from(sourceText.slice(0, sourceText.indexOf(matchedText))).length;
      return { start, end: start + Array.from(matchedText).length };
    };
    const issues = [
      {
        id: 'term-cures',
        category: 'Terminology',
        severity: 'CRITICAL',
        rule: 'Prohibited Term: cures',
        matched_text: 'cures',
        replacement_text: 'supports',
        auto_fixable: true,
        location: codePointSpan('cures')
      },
      {
        id: 'claim-cures',
        category: 'Compliance & Claims',
        severity: 'CRITICAL',
        rule: 'Unsupported Medical Claims',
        matched_text: 'cures',
        replacement_text: 'may support',
        auto_fixable: true,
        location: codePointSpan('cures')
      },
      {
        id: 'claim-prevents',
        category: 'Compliance & Claims',
        severity: 'CRITICAL',
        rule: 'Unsupported Medical Claims',
        matched_text: 'prevents',
        replacement_text: 'may reduce',
        auto_fixable: true,
        location: codePointSpan('prevents')
      }
    ];
    const appliedIssues = [];
    const cards = issues.map((issue) => IssueCard({
      issue,
      sourceText,
      onApplyFix: (appliedIssue) => appliedIssues.push(appliedIssue)
    }));

    assert.equal(
      cards.map((card) => renderToStaticMarkup(card)).join('').match(/<button\b/g)?.length,
      issues.length
    );
    cards.forEach((card) => findButton(card).props.onClick());
    assert.deepEqual(appliedIssues, issues);

    const staleIssue = {
      ...issues[0],
      location: { start: 0, end: 5 }
    };
    const staleCard = IssueCard({ issue: staleIssue, sourceText, onApplyFix: () => {} });
    assert.doesNotMatch(renderToStaticMarkup(staleCard), /<button\b/);

    const unflaggedIssue = { ...issues[0] };
    delete unflaggedIssue.auto_fixable;
    const unflaggedCard = IssueCard({
      issue: unflaggedIssue,
      sourceText,
      onApplyFix: () => {}
    });
    assert.doesNotMatch(renderToStaticMarkup(unflaggedCard), /<button\b/);
  } finally {
    await vite.close();
  }
});

test('IssueCard shows separate Apply actions for GreenLeaf medical phrase findings', async () => {
  const projectRoot = fileURLToPath(new URL('..', import.meta.url));
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    root: projectRoot,
    logLevel: 'silent',
    server: { middlewareMode: true }
  });
  try {
    const { default: IssueCard } = await vite.ssrLoadModule('/src/components/IssueCard.jsx');
    const sourceText = (
      'GreenLeaf Organics presents its miracle products that cure diseases '
      + 'and prevent health problems.'
    );
    const locationFor = (matchedText) => {
      const start = Array.from(sourceText.slice(0, sourceText.indexOf(matchedText))).length;
      return { start, end: start + Array.from(matchedText).length };
    };
    const issues = [
      {
        id: 'claim-cure-diseases',
        category: 'Compliance & Claims',
        severity: 'CRITICAL',
        rule: 'Approved phrase: cure diseases',
        matched_text: 'cure diseases',
        replacement_text: 'support a balanced and healthy lifestyle',
        auto_fixable: true,
        location: locationFor('cure diseases')
      },
      {
        id: 'claim-prevent-health-problems',
        category: 'Compliance & Claims',
        severity: 'CRITICAL',
        rule: 'Approved phrase: prevent health problems',
        matched_text: 'prevent health problems',
        replacement_text: 'support overall wellness',
        auto_fixable: true,
        location: locationFor('prevent health problems')
      }
    ];
    const applied = [];
    const cards = issues.map((issue) => IssueCard({
      issue,
      sourceText,
      onApplyFix: (selectedIssue, issueId) => {
        applied.push({ issueId, replacement: selectedIssue.replacement_text });
      }
    }));

    const markup = cards.map((card) => renderToStaticMarkup(card)).join('');
    assert.equal(markup.match(/<button\b/g)?.length, 2);
    cards.forEach((card) => findButton(card).props.onClick());
    assert.deepEqual(applied, [
      {
        issueId: 'claim-cure-diseases',
        replacement: 'support a balanced and healthy lifestyle'
      },
      {
        issueId: 'claim-prevent-health-problems',
        replacement: 'support overall wellness'
      }
    ]);
  } finally {
    await vite.close();
  }
});

test('IssueCard applies only medical findings mapped to their complete sentence', async () => {
  const projectRoot = fileURLToPath(new URL('..', import.meta.url));
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    root: projectRoot,
    logLevel: 'silent',
    server: { middlewareMode: true }
  });
  try {
    const { default: IssueCard } = await vite.ssrLoadModule('/src/components/IssueCard.jsx');
    const sourceText = 'GreenLeaf products cure diseases and prevent health problems.';
    const replacement = 'Enjoy food made with natural ingredients.';
    const sentenceSpan = { start: 0, end: Array.from(sourceText).length };
    const mappedIssues = ['cure', 'prevent'].map((triggerText) => ({
      id: `claim-${triggerText}`,
      category: 'Compliance & Claims',
      severity: 'CRITICAL',
      rule: 'Unsupported Medical Claims',
      trigger_text: triggerText,
      matched_text: sourceText,
      replacement_text: replacement,
      auto_fixable: true,
      location: sentenceSpan
    }));
    const manualWordIssue = {
      ...mappedIssues[0],
      id: 'claim-word-only',
      trigger_text: 'cure',
      matched_text: 'cure',
      replacement_text: 'support',
      auto_fixable: false,
      manual_review_required: true,
      manual_review_reason: 'A single-word claim replacement requires manual review to verify sentence grammar.',
      location: {
        start: sourceText.indexOf('cure'),
        end: sourceText.indexOf('cure') + 'cure'.length
      }
    };
    const cards = [...mappedIssues, manualWordIssue].map((issue) =>
      IssueCard({
        issue,
        sourceText,
        onApplyFix: () => {}
      })
    );
    const markup = cards.map(renderToStaticMarkup).join('');

    assert.equal(markup.match(/<button\b/g)?.length, 2);
    assert.match(markup, /Found:<\/span>\s*<span[^>]*>&quot;cure&quot;<\/span>/);
    assert.match(markup, /sentence grammar/);
    assert.doesNotMatch(
      renderToStaticMarkup(cards[2]),
      /<button\b/
    );
  } finally {
    await vite.close();
  }
});
