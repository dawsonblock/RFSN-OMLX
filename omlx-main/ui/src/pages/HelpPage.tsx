import { Section } from '../components/ui';

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-neutral-300 bg-neutral-100 px-1.5 py-0.5 font-mono text-[11px] text-neutral-700">
      {children}
    </kbd>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-neutral-100 px-1 py-0.5 font-mono text-[12px] text-neutral-800">
      {children}
    </code>
  );
}

function Field({
  name,
  required,
  children,
}: {
  name: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="border-l-2 border-neutral-200 pl-3">
      <div className="font-mono text-[12px] font-semibold text-neutral-900">
        {name}
        {required ? (
          <span className="ml-1 text-red-600">*</span>
        ) : (
          <span className="ml-1 text-neutral-400">(optional)</span>
        )}
      </div>
      <div className="text-sm text-neutral-700">{children}</div>
    </div>
  );
}

export default function HelpPage() {
  return (
    <>
      <Section title="What is this?">
        <div className="card space-y-2 text-sm text-neutral-700">
          <p>
            OMLX is a local LLM serving runtime for Apple Silicon Macs, built on{' '}
            <Code>mlx-lm</Code>. This operator UI talks to the same FastAPI process — it
            serves the SPA at <Code>/ui/</Code> and exposes OpenAI-compatible endpoints at{' '}
            <Code>/v1/*</Code>.
          </p>
          <p>
            Everything runs on <Code>127.0.0.1</Code>. No data leaves your machine
            unless you explicitly download a model from HuggingFace.
          </p>
        </div>
      </Section>

      <Section title="First-run checklist">
        <ol className="card list-decimal space-y-2 pl-6 text-sm text-neutral-700">
          <li>
            Open <strong>Models</strong> and pick a model from the catalog. Qwen2.5 0.5B
            Instruct (4-bit) is the smallest — ~400 MB — and good for smoke-testing.
          </li>
          <li>
            Watch the <strong>Active downloads</strong> panel until the task reaches{' '}
            <Code>completed</Code>. You can cancel or retry from there.
          </li>
          <li>
            Go to <strong>Chat</strong>, select the model in the dropdown, type a message,
            and press <Kbd>⌘/Ctrl</Kbd>+<Kbd>Enter</Kbd> or click <em>Send</em>.
          </li>
          <li>
            (Optional) Open <strong>Workspaces → Create workspace</strong> to start a
            persistent session whose KV cache is archived on disk. See the detailed
            field guide below.
          </li>
        </ol>
      </Section>

      <Section title="Creating a workspace — field by field">
        <div className="card space-y-3 text-sm text-neutral-700">
          <p>
            A <strong>workspace</strong> is a persistent <Code>(model, session)</Code>{' '}
            pair. The KV cache is snapshotted on disk so you can resume, fork, export,
            and diff it later. Open <strong>Workspaces → Create workspace</strong> and
            fill the form:
          </p>
          <div className="space-y-3">
            <Field name="model_name" required>
              The model id <em>as it is registered in this runtime</em> — for example{' '}
              <Code>llama</Code>, <Code>qwen</Code>, or whatever alias you loaded. This
              is <em>not</em> the HuggingFace repo id; it is the server-side key you see
              in <Code>/v1/models</Code> and on the <strong>Chat</strong> model dropdown.
            </Field>
            <Field name="session_id" required>
              A short identifier unique <em>within</em> that model — e.g.{' '}
              <Code>notes</Code>, <Code>debug-1</Code>, <Code>ticket-4821</Code>. The
              pair <Code>(model_name, session_id)</Code> must not collide with an
              existing workspace. Treat it like a filename: lowercase, no spaces, no
              slashes.
            </Field>
            <Field name="label">
              Human-readable title shown in the workspaces list. Change it any time —
              it does not affect addressing.
            </Field>
            <Field name="description">
              Free-form notes. A good place for &ldquo;what is this workspace for&rdquo;
              so you know why to keep or prune it six weeks from now.
            </Field>
            <Field name="task_tag">
              Category tag used by pruning policies and export filters (e.g.{' '}
              <Code>eval</Code>, <Code>demo</Code>, <Code>keep</Code>). Optional but
              recommended if you keep many workspaces around.
            </Field>
            <Field name="block_size">
              KV-cache snapshot granularity, in tokens. Leave blank to use the archive
              default (usually 256). Smaller blocks = more snapshots, finer fork
              points, more disk; larger blocks = fewer snapshots, less overhead. Only
              change this if you have a reason.
            </Field>
          </div>
          <p className="text-xs text-neutral-500">
            After creation, click the session id in the workspaces table to open it,
            then <em>Validate</em> to confirm replayability, <em>Lineage</em> to see
            ancestors/descendants, or <em>Fork</em> to branch from a specific turn.
          </p>
        </div>
      </Section>

      <Section title="Context & output length">
        <div className="card space-y-2 text-sm text-neutral-700">
          <p>There are two separate limits to think about:</p>
          <ul className="list-disc space-y-1 pl-6">
            <li>
              <strong>Context window</strong> — the maximum number of prompt tokens the
              model can attend to at once. This is a property of the model itself
              (e.g. Qwen2.5 ≈ 32K, Llama-3.2 ≈ 128K). OMLX does not re-train it. If
              your prompt exceeds the model's native window you will see a rejection
              from the server; pick a longer-context model instead.
            </li>
            <li>
              <strong>Output length</strong> (<Code>max_tokens</Code>) — how many tokens
              the model is allowed to generate in a single reply. This <em>is</em>{' '}
              adjustable per request. On the <strong>Chat</strong> tab, use the{' '}
              <em>Max tokens</em> field to raise it (e.g. <Code>2048</Code>,{' '}
              <Code>4096</Code>, <Code>8192</Code>). Leave it blank to use the server
              default.
            </li>
          </ul>
          <p className="text-xs text-neutral-500">
            Tip: long outputs cost time and memory. On a 16 GB Mac, staying under
            4096 output tokens keeps latency comfortable on small 4-bit models.
          </p>
          <p>
            For long-running conversations, prefer a <strong>workspace</strong> (see
            above) — it reuses the KV cache between turns, so each new turn only pays
            for the new prompt, not the whole transcript.
          </p>
        </div>
      </Section>

      <Section title="Tabs">
        <div className="card space-y-3 text-sm text-neutral-700">
          <div>
            <div className="font-semibold text-neutral-900">Workspaces</div>
            <p>
              Browse, pin, validate, fork, and inspect session archives. Each workspace
              is a <Code>(model, session)</Code> pair with a KV-cache lineage tree.
              Use <em>Validate</em> to check replayability and <em>Lineage</em> to see
              ancestors and descendants.
            </p>
          </div>
          <div>
            <div className="font-semibold text-neutral-900">Chat</div>
            <p>
              OpenAI-compatible chat against any loaded model. Streams tokens over SSE
              by default (toggle <em>Stream</em> off to use a single blocking request).{' '}
              <em>Stop</em> cancels the in-flight request via <Code>AbortController</Code>.
              Adjust <em>Temperature</em> and <em>Max tokens</em> inline. The system
              prompt is editable in place.
            </p>
          </div>
          <div>
            <div className="font-semibold text-neutral-900">Models</div>
            <p>
              Curated catalog of MLX-compatible models plus a custom{' '}
              <Code>org/name</Code> field for anything else on HuggingFace. Paste an HF
              token only if you need gated repos — it stays in this browser tab.
            </p>
          </div>
          <div>
            <div className="font-semibold text-neutral-900">Transfers</div>
            <p>
              Export a workspace as a portable bundle, or import one that was produced
              on another machine. Use <em>Inspect</em> before import to see the envelope
              metadata. <em>Pin</em> a bundle to keep prune/maintenance from touching it.
            </p>
          </div>
          <div>
            <div className="font-semibold text-neutral-900">Maintenance</div>
            <p>
              Review archive stats, then run <em>Dry run</em> to preview which
              workspaces and bundles are pruneable. Execute only happens after a
              dry-run produces a <Code>plan_signature</Code>, which acts as the
              confirmation token.
            </p>
          </div>
          <div>
            <div className="font-semibold text-neutral-900">Settings</div>
            <p>
              Shows the environment: OMLX version, archive root, supported manifest
              versions, cache layout, and an on-demand health check.
            </p>
          </div>
        </div>
      </Section>

      <Section title="Tips">
        <ul className="card list-disc space-y-1 pl-6 text-sm text-neutral-700">
          <li>
            The UI never auto-loads a model into GPU — the first chat message does.
            Expect a brief delay on the first send after a process restart.
          </li>
          <li>
            If a chat returns 404, no model is loaded at that id. Check{' '}
            <strong>Models</strong> → Installed, or wait for an active download to
            finish.
          </li>
          <li>
            4-bit MLX quants are the sweet spot on Apple Silicon. 0.5B–3B comfortably
            fit on 16 GB Macs; 7B usually needs 24 GB+ of unified memory.
          </li>
          <li>
            If replies look truncated, raise <em>Max tokens</em> on the Chat tab. If
            the prompt itself is too big, switch to a longer-context model.
          </li>
          <li>
            Destructive actions (prune, delete, reset) always require an explicit
            confirmation dialog.
          </li>
        </ul>
      </Section>

      <Section title="Command-line cheat sheet">
        <div className="card space-y-1 font-mono text-xs text-neutral-800">
          <div>omlx serve --host 127.0.0.1 --port 8765</div>
          <div>curl -s http://127.0.0.1:8765/v1/models</div>
          <div>curl -sN http://127.0.0.1:8765/v1/chat/completions \</div>
          <div className="pl-4">-H 'content-type: application/json' \</div>
          <div className="pl-4">
            -d '{'{'}"model":"&lt;id&gt;","messages":[{'{'}"role":"user","content":"hi"{'}'}],"max_tokens":4096,"stream":true{'}'}'
          </div>
        </div>
      </Section>

      <Section title="Where things live">
        <ul className="card list-disc space-y-1 pl-6 text-sm text-neutral-700">
          <li>
            SPA + API: single FastAPI process, same port. UI routes under{' '}
            <Code>/ui/</Code>, operator API under <Code>/ui/api/</Code>,
            OpenAI-compatible endpoints under <Code>/v1/</Code>.
          </li>
          <li>
            Archive root and cache dirs are shown on <strong>Settings</strong>.
          </li>
          <li>
            Everything logged to <Code>~/.omlx/logs/</Code>.
          </li>
        </ul>
      </Section>
    </>
  );
}
