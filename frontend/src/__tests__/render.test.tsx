/**
 * Smoke test: the whole component tree mounts and produces markup.
 *
 * Server rendering, so no jsdom dependency. Effects do not run, which means
 * this proves the render path is sound (imports, JSX, prop types, chart setup)
 * rather than the polling behaviour -- that is covered by store.test.ts.
 */
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import App from '../App';
import Markdown from '../components/Markdown';

describe('render', () => {
  it('mounts the full three-region layout', () => {
    const html = renderToString(<App />);
    // The rail, the empty-state splash, and each instrument panel heading.
    expect(html).toContain('Privis');
    expect(html).toContain('Router');
    expect(html).toContain('Agent trace');
    expect(html).toContain('Artifacts');
    // Idle and untouched, the instrument panels render the sample fixture
    // rather than three empty boxes -- and say so.
    expect(html).toContain('sample');
    expect(html).toContain('search_documents');
    expect(html).toContain('wall-loss-summary.docx');
  });

  it('renders markdown tables and highlights fenced code', () => {
    const html = renderToString(
      <Markdown>
        {'| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nprint(1 + 1)\n```\n'}
      </Markdown>,
    );
    expect(html).toContain('<table');
    expect(html).toContain('<th');
    // Prism ran: a token span means the grammar was found and applied.
    expect(html).toContain('token');
    expect(html).toContain('copy');
  });
});
