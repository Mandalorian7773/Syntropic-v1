/**
 * Markdown renderer. Owner: person 1.
 *
 * react-markdown + remark-gfm for tables and strikethrough. No rehype-raw:
 * model output is never trusted as HTML.
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CodeBlock from './CodeBlock';

export default function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed text-steel-200">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: (p) => <p className="my-2 first:mt-0 last:mb-0" {...p} />,
          h1: (p) => <h1 className="mb-2 mt-4 font-mono text-base font-semibold
                                    text-steel-100 first:mt-0" {...p} />,
          h2: (p) => <h2 className="mb-2 mt-4 font-mono text-sm font-semibold
                                    uppercase tracking-wide text-steel-100
                                    first:mt-0" {...p} />,
          h3: (p) => <h3 className="mb-1 mt-3 font-mono text-sm font-semibold
                                    text-steel-200 first:mt-0" {...p} />,
          ul: (p) => <ul className="my-2 list-disc space-y-1 pl-5
                                    marker:text-steel-600" {...p} />,
          ol: (p) => <ol className="my-2 list-decimal space-y-1 pl-5
                                    marker:text-steel-600" {...p} />,
          strong: (p) => <strong className="font-semibold text-steel-100" {...p} />,
          a: ({ href, ...rest }) => (
            // Anything the model emits as a link stays inert: this app is
            // air-gapped and a live external href would be a contradiction.
            <span className="text-accent underline decoration-dotted"
                  title={href} {...rest} />
          ),
          blockquote: (p) => (
            <blockquote className="my-2 border-l-2 border-steel-700 pl-3
                                   text-steel-400" {...p} />
          ),
          hr: () => <hr className="my-3 border-steel-800" />,
          table: (p) => (
            <div className="my-3 overflow-x-auto scroll-thin border
                            border-steel-800">
              <table className="w-full border-collapse font-mono text-tiny" {...p} />
            </div>
          ),
          thead: (p) => <thead className="bg-steel-850" {...p} />,
          th: (p) => (
            <th className="border-b border-steel-800 px-2 py-1 text-left
                           font-semibold uppercase tracking-wide
                           text-steel-400" {...p} />
          ),
          td: (p) => (
            <td className="border-b border-steel-850 px-2 py-1 text-steel-200"
                {...p} />
          ),
          code: ({ className, children, ...rest }) => {
            const text = String(children ?? '').replace(/\n$/, '');
            const match = /language-(\w+)/.exec(className ?? '');
            // react-markdown gives block code a language- class or a newline.
            if (match || text.includes('\n')) {
              return <CodeBlock code={text} language={match?.[1] ?? null} />;
            }
            return (
              <code className="rounded-sm bg-steel-800 px-1 py-0.5 font-mono
                               text-[0.85em] text-accent" {...rest}>
                {children}
              </code>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
