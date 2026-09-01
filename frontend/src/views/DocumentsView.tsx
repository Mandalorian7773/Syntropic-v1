/**
 * Documents view. Owner: person 1.
 * Upload dropzone plus the ingested corpus with page and chunk counts.
 */
import { useCallback, useEffect, useState } from 'react';
import type { DragEvent } from 'react';
import type { DocumentInfo } from '../types/events';
import { documents, uploadDocument } from '../api/rest';
import { Dot, bytes } from '../components/ui';

export default function DocumentsView() {
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    try {
      setDocs(await documents());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Poll while anything is still ingesting, so status resolves without a
    // manual refresh. Cheap: it is a local request over loopback.
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  const accept = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    setUploading(files.map((f) => f.name));
    for (const file of files) {
      try {
        await uploadDocument(file);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
    setUploading([]);
    void refresh();
  }, [refresh]);

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    void accept(Array.from(e.dataTransfer.files));
  }

  const totals = docs.reduce(
    (acc, d) => ({
      pages: acc.pages + d.pages,
      chunks: acc.chunks + d.chunks,
      size: acc.size + d.size_bytes,
    }),
    { pages: 0, chunks: 0, size: 0 },
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto scroll-thin p-6">
      <header className="mb-4 flex items-baseline justify-between">
        <h1 className="font-mono text-sm uppercase tracking-widest text-steel-100">
          Document corpus
        </h1>
        <span className="font-mono text-tiny text-steel-500">
          {docs.length} docs · {totals.pages} pages · {totals.chunks} chunks ·{' '}
          {bytes(totals.size)}
        </span>
      </header>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`mb-5 border border-dashed p-6 text-center transition-colors
                    ${dragging
                      ? 'border-accent bg-accent-deep/40'
                      : 'border-steel-700 bg-steel-900/60'}`}
      >
        <p className="font-mono text-tiny text-steel-300">
          Drop PDFs, spreadsheets or scans here
        </p>
        <p className="mt-1 font-mono text-micro text-steel-600">
          Ingestion, OCR and indexing all run on this machine
        </p>
        <label className="mt-3 inline-block cursor-pointer border
                          border-accent-dim bg-accent-deep px-3 py-1 font-mono
                          text-tiny text-accent hover:bg-accent
                          hover:text-steel-950">
          Choose files
          <input
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              void accept(Array.from(e.target.files ?? []));
              e.target.value = '';
            }}
          />
        </label>
        {uploading.length > 0 && (
          <p className="mt-2 font-mono text-tiny text-work">
            uploading {uploading.join(', ')}…
          </p>
        )}
      </div>

      {error && (
        <p className="mb-3 border border-fault-dim bg-fault-deep px-3 py-2
                      font-mono text-tiny text-fault">
          {error}
        </p>
      )}

      <table className="w-full border-collapse font-mono text-tiny">
        <thead>
          <tr className="border-b border-steel-700">
            {['Document', 'Pages', 'Chunks', 'Size', 'Ingested', 'Status'].map(
              (h, i) => (
                <th
                  key={h}
                  className={`label py-1.5 ${i === 0 ? 'text-left' : 'text-right'}`}
                >
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {docs.length === 0 && (
            <tr>
              <td colSpan={6} className="py-8 text-center text-steel-600">
                No documents ingested yet.
              </td>
            </tr>
          )}
          {docs.map((d) => (
            <tr key={d.doc_id}
                className="border-b border-steel-850 hover:bg-steel-900">
              <td className="py-1.5 text-steel-200">{d.filename}</td>
              <td className="py-1.5 text-right tabular-nums text-steel-400">
                {d.pages}
              </td>
              <td className="py-1.5 text-right tabular-nums text-steel-400">
                {d.chunks || '—'}
              </td>
              <td className="py-1.5 text-right tabular-nums text-steel-400">
                {bytes(d.size_bytes)}
              </td>
              <td className="py-1.5 text-right tabular-nums text-steel-500">
                {new Date(d.ingested_at * 1000).toLocaleDateString()}
              </td>
              <td className="py-1.5 text-right">
                <span className="inline-flex items-center gap-1.5">
                  <Dot tone={
                    d.status === 'indexed' ? 'iso'
                    : d.status === 'failed' ? 'fault' : 'work'
                  } />
                  <span className={
                    d.status === 'indexed' ? 'text-iso'
                    : d.status === 'failed' ? 'text-fault' : 'text-work'
                  }>
                    {d.status}
                  </span>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
