import { useEffect, useRef, useState } from 'react';
import { Section, ErrorBox } from '../components/ui';
import {
  listModels,
  chatCompletion,
  streamChatCompletion,
  ChatError,
  type ChatMessage,
} from '../lib/chat';

interface UIMessage extends ChatMessage {
  id: string;
}

function newId() {
  return Math.random().toString(36).slice(2, 10);
}

export default function ChatPage() {
  const [models, setModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState<unknown>(null);
  const [model, setModel] = useState('');
  const [system, setSystem] = useState('You are a helpful assistant.');
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [streaming, setStreaming] = useState(true);
  const [temperature, setTemperature] = useState('0.7');
  const [maxTokens, setMaxTokens] = useState('4096');
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState<unknown>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    listModels()
      .then((ids) => {
        if (!alive) return;
        setModels(ids);
        setModel((prev) => prev || ids[0] || '');
      })
      .catch((err) => {
        if (!alive) return;
        setModelsError(err);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const payload = (userText: string): ChatMessage[] => {
    const out: ChatMessage[] = [];
    if (system.trim()) out.push({ role: 'system', content: system });
    for (const m of messages) out.push({ role: m.role, content: m.content });
    out.push({ role: 'user', content: userText });
    return out;
  };

  const onSend = async () => {
    const text = input.trim();
    if (!text || !model || busy) return;
    setSendError(null);
    setInput('');
    const userMsg: UIMessage = { id: newId(), role: 'user', content: text };
    const assistantId = newId();
    setMessages((prev) => [
      ...prev,
      userMsg,
      { id: assistantId, role: 'assistant', content: '' },
    ]);
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const temp = Number(temperature);
      const mt = maxTokens.trim() === '' ? undefined : Number(maxTokens);
      const req = {
        model,
        messages: payload(text),
        temperature: Number.isFinite(temp) ? temp : 0.7,
        ...(mt !== undefined && Number.isFinite(mt) && mt > 0 ? { max_tokens: mt } : {}),
      };
      if (streaming) {
        for await (const acc of streamChatCompletion(req, ac.signal)) {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content: acc } : m)),
          );
        }
      } else {
        const reply = await chatCompletion(req, ac.signal);
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content: reply } : m)),
        );
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId && !m.content
              ? { ...m, content: '(cancelled)' }
              : m,
          ),
        );
      } else {
        setSendError(err);
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  const onStop = () => {
    abortRef.current?.abort();
  };

  const onClear = () => {
    setMessages([]);
    setSendError(null);
  };

  return (
    <>
      <Section title="Chat">
        <div className="card flex flex-col gap-2 md:flex-row md:items-end">
          <div className="flex-1">
            <label className="label">Model</label>
            {models.length === 0 && !modelsError && (
              <div className="text-xs text-neutral-500">No models loaded yet.</div>
            )}
            {modelsError ? <ErrorBox error={modelsError} /> : null}
            {models.length > 0 && (
              <select
                className="input mt-1 w-full"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div>
            <label className="label">Temperature</label>
            <input
              className="input mt-1 w-24"
              type="number"
              step="0.05"
              min="0"
              max="2"
              value={temperature}
              onChange={(e) => setTemperature(e.target.value)}
            />
          </div>
          <div>
            <label className="label">Max tokens</label>
            <div className="mt-1 flex items-center gap-1">
              <input
                className="input w-28"
                type="number"
                min="1"
                placeholder="default"
                value={maxTokens}
                onChange={(e) => setMaxTokens(e.target.value)}
              />
              {[2048, 4096, 8192, 16384, 32768].map((n) => (
                <button
                  key={n}
                  type="button"
                  className="btn px-1.5 py-0.5 text-[11px]"
                  onClick={() => setMaxTokens(String(n))}
                  title={`Set max tokens to ${n}`}
                >
                  {n >= 1024 ? `${n / 1024}K` : String(n)}
                </button>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-1 text-sm">
            <input
              type="checkbox"
              checked={streaming}
              onChange={(e) => setStreaming(e.target.checked)}
            />
            Stream
          </label>
          <button type="button" className="btn text-xs" onClick={onClear} disabled={busy}>
            Clear
          </button>
        </div>
      </Section>

      <Section title="System prompt">
        <textarea
          className="input w-full"
          rows={2}
          value={system}
          onChange={(e) => setSystem(e.target.value)}
          placeholder="System instructions…"
        />
      </Section>

      <Section title="Conversation">
        <div
          ref={logRef}
          className="card max-h-[50vh] space-y-3 overflow-y-auto"
          data-testid="chat-log"
        >
          {messages.length === 0 && (
            <div className="text-sm text-neutral-500">No messages yet. Ask something below.</div>
          )}
          {messages.map((m) => (
            <div key={m.id} className="flex flex-col gap-1">
              <div className="text-[11px] uppercase tracking-wider text-neutral-500">
                {m.role}
              </div>
              <div
                className={`whitespace-pre-wrap rounded border p-2 text-sm ${
                  m.role === 'user'
                    ? 'border-blue-200 bg-blue-50'
                    : 'border-neutral-200 bg-neutral-50'
                }`}
              >
                {m.content || (m.role === 'assistant' && busy ? '…' : '')}
              </div>
            </div>
          ))}
        </div>
        {sendError ? (
          <div className="mt-2">
            <ErrorBox error={sendError} />
            {sendError instanceof ChatError && sendError.status === 404 && (
              <div className="mt-1 text-xs text-neutral-500">
                Tip: no model is loaded at that id. Go to Models and download one first.
              </div>
            )}
          </div>
        ) : null}
      </Section>

      <Section title="Message">
        <div className="card flex flex-col gap-2">
          <textarea
            className="input w-full"
            rows={3}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message… (⌘/Ctrl+Enter to send)"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault();
                onSend();
              }
            }}
            disabled={busy}
          />
          <div className="flex justify-end gap-2">
            {busy && (
              <button type="button" className="btn text-xs" onClick={onStop}>
                Stop
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary text-xs disabled:opacity-50"
              onClick={onSend}
              disabled={busy || !model || !input.trim()}
            >
              {busy ? 'Sending…' : 'Send'}
            </button>
          </div>
        </div>
      </Section>
    </>
  );
}
