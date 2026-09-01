/**
 * Code block with copy button. Owner: person 1.
 *
 * Prism rather than Shiki: Prism is a few KB of pure JS with the languages
 * imported as ES modules, so vite bundles everything and nothing is fetched at
 * runtime. Shiki's WASM + theme JSON would be more to vendor for no gain here.
 * The theme lives in index.css against our palette, not an imported stylesheet.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
// prism-core, NOT 'prismjs': the main entry bundles the file-highlight plugin,
// which XHRs any <pre data-src>. We never emit that attribute, but an unused
// network path in an air-gapped app is a latent contradiction -- and dropping
// it is also the only XMLHttpRequest in the bundle gone.
import Prism from 'prismjs/components/prism-core';
// Languages are explicit imports so the bundler includes them. Adding one is
// an import line here -- never a runtime fetch.
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-sql';

const ALIASES: Record<string, string> = {
  py: 'python', sh: 'bash', shell: 'bash', yml: 'yaml',
  js: 'javascript', ts: 'typescript',
};

export default function CodeBlock({ code, language }: {
  code: string; language: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  const lang = language ? (ALIASES[language] ?? language) : null;
  const grammar = lang ? Prism.languages[lang] : undefined;

  const html = useMemo(() => {
    if (!grammar || !lang) return null;
    return Prism.highlight(code, grammar, lang);
  }, [code, grammar, lang]);

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      return; // clipboard blocked; say nothing rather than throw a dialog
    }
    setCopied(true);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="group relative my-2 border border-steel-800 bg-steel-950">
      <div className="flex items-center justify-between border-b border-steel-850
                      bg-steel-900 px-2 py-1">
        <span className="label">{lang ?? 'text'}</span>
        <button
          type="button"
          onClick={copy}
          className={`font-mono text-micro uppercase tracking-widest
                      ${copied ? 'text-iso' : 'text-steel-500 hover:text-accent'}`}
        >
          {copied ? 'copied' : 'copy'}
        </button>
      </div>
      <pre className="overflow-x-auto scroll-thin p-3 font-mono text-tiny
                      leading-relaxed">
        {html !== null
          // Prism output; the input is model text rendered as code, never HTML.
          ? <code dangerouslySetInnerHTML={{ __html: html }} />
          : <code>{code}</code>}
      </pre>
    </div>
  );
}
