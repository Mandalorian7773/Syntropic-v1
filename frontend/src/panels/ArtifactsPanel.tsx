/**
 * Artifacts. Owner: person 1.
 *
 * This is where judges see that the system produced a real Word file rather
 * than a chat reply, so the download button is the loudest thing on the card
 * and the filename is never truncated into uselessness.
 */
import { useSession } from '../store/session';
import { artifactUrl } from '../api/rest';
import { Empty, Panel, bytes } from '../components/ui';

/** Extension -> short tag. A tag beats an icon font we would have to vendor. */
function kind(filename: string, mime: string): { tag: string; tone: string } {
  const ext = filename.split('.').pop()?.toLowerCase() ?? '';
  if (ext === 'docx' || mime.includes('wordprocessing'))
    return { tag: 'DOCX', tone: 'text-accent border-accent-dim' };
  if (ext === 'xlsx' || mime.includes('spreadsheet'))
    return { tag: 'XLSX', tone: 'text-iso border-iso-dim' };
  if (ext === 'pdf') return { tag: 'PDF', tone: 'text-fault border-fault-dim' };
  if (['png', 'jpg', 'jpeg', 'svg'].includes(ext))
    return { tag: 'IMG', tone: 'text-work border-work-dim' };
  if (['csv', 'json', 'txt', 'md'].includes(ext))
    return { tag: ext.toUpperCase(), tone: 'text-steel-300 border-steel-600' };
  return { tag: 'FILE', tone: 'text-steel-400 border-steel-600' };
}

export default function ArtifactsPanel({ className = '' }: { className?: string }) {
  const artifacts = useSession((s) => s.artifacts);

  return (
    <Panel
      title="Artifacts"
      className={className}
      right={
        artifacts.length > 0 ? (
          <span className="font-mono text-tiny text-steel-400">
            {artifacts.length}
          </span>
        ) : null
      }
    >
      {artifacts.length === 0 ? (
        <Empty>No files produced yet.</Empty>
      ) : (
        <ul className="divide-y divide-steel-850">
          {artifacts.map((a) => {
            const k = kind(a.filename, a.mime);
            return (
              <li key={a.artifact_id} className="flex items-center gap-2.5 px-3 py-2">
                <span
                  className={`flex h-8 w-9 shrink-0 items-center justify-center
                              border ${k.tone} font-mono text-micro font-bold`}
                >
                  {k.tag}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-tiny text-steel-100"
                     title={a.filename}>
                    {a.filename}
                  </p>
                  <p className="font-mono text-micro text-steel-500">
                    {bytes(a.size_bytes)}
                  </p>
                </div>
                {/* Plain anchor with `download`: no JS, works with any backend
                    that sets Content-Disposition, and cannot fail silently. */}
                <a
                  href={artifactUrl(a)}
                  download={a.filename}
                  className="shrink-0 border border-accent-dim bg-accent-deep
                             px-2 py-1 font-mono text-tiny text-accent
                             hover:bg-accent hover:text-steel-950"
                >
                  Download
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
