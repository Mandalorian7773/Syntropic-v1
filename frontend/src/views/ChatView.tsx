/**
 * Chat view: message list + composer. Owner: person 1.
 *
 * Two behaviours worth calling out:
 *
 *  - Auto-scroll follows the stream, but STOPS the moment the user scrolls up,
 *    and a "jump to latest" pill appears. Nothing is more irritating on a
 *    projector than a list that yanks itself back down while someone reads.
 *  - While waiting, the assistant bubble shows what the machine is doing
 *    (routing / swapping / running a tool) rather than a bare spinner. A
 *    40-second answer needs a reason to look alive.
 */
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useSession } from '../store/session';
import type { ChatMessage } from '../store/session';
import Markdown from '../components/Markdown';
import { ModelPicker, SwapProgress } from '../components/ModelPicker';
import { uploadDocument } from '../api/rest';
import { Dot, ms } from '../components/ui';

export default function ChatView() {
  const messages = useSession((s) => s.messages);
  const phase = useSession((s) => s.phase);
  const lastRun = useSession((s) => s.lastRun);

  const scroller = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  // Follow the stream only while pinned to the bottom.
  useEffect(() => {
    if (!pinned) return;
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  });

  function onScroll() {
    const el = scroller.current;
    if (!el) return;
    // 40px of slack: "close enough to the bottom" survives a streaming reflow.
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  }

  function toLatest() {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    setPinned(true);
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scroller}
        onScroll={onScroll}
        className="min-h-0 flex-1 space-y-5 overflow-y-auto scroll-thin px-6 py-5"
      >
        {messages.length === 0 && <Splash />}
        {messages.map((m) => (
          <Bubble key={m.id} message={m} />
        ))}
        {lastRun && phase === 'idle' && <RunFooter />}
      </div>

      {!pinned && messages.length > 0 && (
        <button
          type="button"
          onClick={toLatest}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 border
                     border-steel-700 bg-steel-850 px-3 py-1 font-mono text-tiny
                     text-steel-200 shadow-lg hover:border-accent-dim
                     hover:text-accent"
        >
          ↓ jump to latest
        </button>
      )}

      {/* Above the composer, so a fifteen-second swap is the last thing you
          saw move rather than something you have to go looking for. */}
      <SwapProgress />
      <Composer />
    </div>
  );
}

function Splash() {
  return (
    <div className="mx-auto max-w-lg pt-10 text-center">
      <p className="font-mono text-micro uppercase tracking-[0.3em] text-steel-600">
        Sovereign agentic workbench
      </p>
      <p className="mt-3 text-sm text-steel-400">
        Everything runs on this machine. No request leaves it.
      </p>
      <div className="mt-6 grid grid-cols-2 gap-2 text-left">
        {[
          ['Document', 'What is the max permissible wall loss in the SOP?'],
          ['Code', 'Write a python script to total downtime per unit'],
          ['Failure', 'Scan every page — make this fail with a timeout'],
          ['Simple', 'Who signs off a hot work permit?'],
        ].map(([tag, text]) => (
          <Suggestion key={tag} tag={tag} text={text} />
        ))}
      </div>
    </div>
  );
}

function Suggestion({ tag, text }: { tag: string; text: string }) {
  const send = useSession((s) => s.send);
  return (
    <button
      type="button"
      onClick={() => send(text)}
      className="border border-steel-800 bg-steel-900 p-2 text-left
                 hover:border-accent-dim hover:bg-steel-850"
    >
      <span className="label">{tag}</span>
      <p className="mt-1 text-tiny text-steel-300">{text}</p>
    </button>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const phase = useSession((s) => s.phase);
  const messages = useSession((s) => s.messages);
  const isLast = messages[messages.length - 1]?.id === message.id;
  const streaming = isLast && message.role === 'assistant' && phase !== 'idle';

  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] border border-steel-700 bg-steel-800 px-3
                        py-2 text-sm text-steel-100">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      <div className="w-1 shrink-0 bg-accent-dim" aria-hidden />
      <div className="min-w-0 flex-1">
        {message.content ? (
          <Markdown>{message.content}</Markdown>
        ) : streaming ? (
          <Thinking />
        ) : (
          <p className="font-mono text-tiny text-steel-600">no output</p>
        )}

        {streaming && message.content && <Caret />}

        {message.citations.length > 0 && (
          <Citations message={message} />
        )}

        {message.stopReason && message.stopReason !== 'final_answer' && (
          <p
            className={`mt-2 inline-block border px-1.5 py-0.5 font-mono
                        text-micro uppercase tracking-widest ${
              message.stopReason === 'cancelled'
                ? 'border-steel-600 text-steel-400'
                : 'border-fault-dim text-fault'
            }`}
          >
            stopped: {message.stopReason.replace('_', ' ')}
          </p>
        )}
      </div>
    </div>
  );
}

/** What the machine is doing right now, in words. Never a bare spinner. */
function Thinking() {
  const phase = useSession((s) => s.phase);
  const trace = useSession((s) => s.trace);
  const running = [...trace].reverse().find((t) => t.ok === null && t.tool);

  const text =
    phase === 'waiting' ? 'contacting the model server'
    : phase === 'routing' ? 'classifying the task'
    : phase === 'swapping' ? 'swapping the resident model'
    : running ? `running ${running.tool}`
    : 'generating';

  return (
    <p className="flex items-center gap-2 font-mono text-tiny text-steel-400">
      <Dot tone="work" />
      {text}
      <span className="animate-pulse-slow text-steel-600">…</span>
    </p>
  );
}

function Caret() {
  return (
    <span className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5
                     animate-pulse-slow bg-accent" aria-hidden />
  );
}

function Citations({ message }: { message: ChatMessage }) {
  return (
    <div className="mt-3 border-t border-steel-850 pt-2">
      <p className="label mb-1.5">Sources</p>
      <ol className="space-y-1.5">
        {message.citations.map((c, i) => (
          <li key={`${c.doc_id}-${c.page}-${i}`} className="flex gap-2">
            <span className="shrink-0 font-mono text-tiny text-steel-600">
              [{i + 1}]
            </span>
            <div className="min-w-0">
              <p className="font-mono text-tiny text-steel-300">
                <span className="text-accent">{c.filename}</span>
                <span className="text-steel-500"> · p.{c.page}</span>
                <span className="ml-1.5 text-steel-600 tabular-nums">
                  {c.score.toFixed(2)}
                </span>
              </p>
              <p className="text-tiny italic leading-relaxed text-steel-500">
                “{c.snippet}”
              </p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function RunFooter() {
  const run = useSession((s) => s.lastRun);
  if (!run) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t
                    border-steel-850 pt-2 font-mono text-micro uppercase
                    tracking-widest text-steel-600">
      <span>stop: <span className="text-steel-400">
        {run.stopReason.replace('_', ' ')}</span></span>
      <span>steps: <span className="text-steel-400">{run.stepsUsed}</span></span>
      <span>tok in: <span className="text-steel-400">{run.tokensIn}</span></span>
      <span>tok out: <span className="text-steel-400">{run.tokensOut}</span></span>
      <span>latency: <span className="text-steel-400">{ms(run.latencyMs)}</span></span>
    </div>
  );
}

function Composer() {
  const send = useSession((s) => s.send);
  const stop = useSession((s) => s.stop);
  const phase = useSession((s) => s.phase);
  const busy = phase !== 'idle';

  const pushError = useSession((s) => s.pushError);
  const [text, setText] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  // Ingest is synchronous and OCR is slow: a scanned page is seconds, a
  // 20-page scan over a minute. Naming the file being read beats a spinner.
  const [uploading, setUploading] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  // Grow with content up to a ceiling, so a long prompt does not eat the chat.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [text]);

  async function submit() {
    if (busy || uploading || !text.trim()) return;

    // Attached files are INGESTED before the message goes, not carried with
    // it. Until now they were collected into state, shown as chips, and then
    // dropped on the floor by setFiles([]) -- so "summarise this PDF" reached
    // a corpus the PDF had never entered, and the agent answered about the
    // other documents instead. Ingesting first is what makes the file
    // reachable by search_documents and read_document at all.
    let preamble = '';
    if (files.length > 0) {
      const ingested: string[] = [];
      for (const file of files) {
        setUploading(file.name);
        try {
          const doc = await uploadDocument(file);
          ingested.push(`${doc.filename} (${doc.pages} page${doc.pages === 1 ? '' : 's'})`);
        } catch (err) {
          setUploading(null);
          pushError(
            `could not ingest ${file.name}: ${err instanceof Error ? err.message : String(err)}`,
            'UPLOAD',
          );
          return; // keep the chips and the text so the send can be retried
        }
      }
      setUploading(null);
      // Name the file in the message. "Summarise it" is unanswerable against a
      // nine-document corpus; the model needs to know which one just arrived.
      preamble =
        `[The user has just uploaded and ingested: ${ingested.join(', ')}. ` +
        `Use search_documents or read_document on it to answer.]\n\n`;
    }

    send(preamble + text);
    setText('');
    setFiles([]);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter is a newline.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="shrink-0 border-t border-steel-800 bg-steel-900 p-3">
      {files.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-1.5">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`}
                className="flex items-center gap-1.5 border border-steel-700
                           bg-steel-850 px-2 py-0.5 font-mono text-tiny
                           text-steel-300">
              {f.name}
              <button
                type="button"
                onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                className="text-steel-600 hover:text-fault"
                aria-label={`remove ${f.name}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-end gap-2">
        <input
          ref={picker}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            setFiles((prev) => [...prev, ...Array.from(e.target.files ?? [])]);
            e.target.value = '';
          }}
        />
        <button
          type="button"
          onClick={() => picker.current?.click()}
          disabled={busy}
          title="Attach a file"
          className="h-9 w-9 shrink-0 border border-steel-700 bg-steel-850
                     font-mono text-base text-steel-400 hover:border-accent-dim
                     hover:text-accent disabled:opacity-40"
        >
          +
        </button>

        <textarea
          ref={box}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={busy}
          placeholder={busy ? 'streaming…' : 'Ask about a document, or request a script.'}
          className="min-h-[36px] flex-1 resize-none border border-steel-700
                     bg-steel-950 px-3 py-2 text-sm text-steel-100
                     placeholder:text-steel-600 focus:border-accent-dim
                     focus:outline-none disabled:opacity-60"
        />

        {busy ? (
          <button
            type="button"
            onClick={stop}
            className="h-9 shrink-0 border border-fault-dim bg-fault-deep px-4
                       font-mono text-tiny uppercase tracking-widest text-fault
                       hover:bg-fault hover:text-steel-950"
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim() || uploading !== null}
            className="h-9 shrink-0 border border-accent-dim bg-accent-deep px-4
                       font-mono text-tiny uppercase tracking-widest text-accent
                       hover:bg-accent hover:text-steel-950
                       disabled:opacity-30 disabled:hover:bg-accent-deep
                       disabled:hover:text-accent"
          >
            {uploading ? 'Reading…' : 'Send'}
          </button>
        )}
      </div>

      <div className="mt-1.5 flex items-center gap-2">
        <ModelPicker />
        <p className="font-mono text-micro text-steel-600">
          {uploading
            ? `reading ${uploading} — OCR runs on CPU, a scanned page takes a few seconds`
            : 'Enter to send · Shift+Enter for a newline · attach a PDF to ask about it'}
        </p>
      </div>
    </div>
  );
}
